"""Zones API (PRD 14.1, 18.2): CRUD with roles, geometry checks, versions, and the zone file the engine reads."""

import json
import sys

from backend.app import config

ENGINE_TESTS = config.ENGINE_DIR / "tests"
BOX = [[20, -3], [40, -3], [40, 3], [20, 3]]


def zone(client, auth, user="planner", **kw):
    body = {"scene": "Town05", "name": "bank front", "type": "no_parking", "polygon": BOX, "grace_s": 20, **kw}
    return client.post("/api/zones", headers=auth(user), json=body)


def test_create_list_read(client, auth):
    r = zone(client, auth)
    assert r.status_code == 201, r.text
    z = r.json()
    p = z["properties"]
    assert z["id"].startswith("z_") and p["scene"] == "Town05" and p["type"] == "no_parking" and p["grace_s"] == 20
    assert p["version"] == 1 and p["created_by"] == "planner" and p["active"] and p["area_m2"] == 120.0
    ring = z["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    fc = client.get("/api/zones", headers=auth("officer"), params={"scene": "town05"}).json()  # scene case-insensitive
    assert fc["type"] == "FeatureCollection" and z["id"] in [f["id"] for f in fc["features"]]
    assert client.get(f"/api/zones/{z['id']}", headers=auth("officer")).json()["properties"]["name"] == "bank front"
    assert client.get("/api/zones/z_nope", headers=auth("officer")).status_code == 404
    assert client.get("/api/zones").status_code == 401


def test_roles(client, auth):
    assert zone(client, auth, "officer").status_code == 403
    assert zone(client, auth, "operator").status_code == 403
    assert zone(client, auth, "admin").status_code == 201
    zid = zone(client, auth).json()["id"]
    assert client.put(f"/api/zones/{zid}", headers=auth("officer"), json={"name": "x"}).status_code == 403
    assert client.delete(f"/api/zones/{zid}", headers=auth("planner")).status_code == 403  # deactivate: ADMIN only


def test_geometry_and_params_checked(client, auth):
    bad = [{"polygon": [[0, 0], [1, 1]]},                               # < 3 vertices
           {"polygon": [[0, 0], [10, 10], [10, 0], [0, 10]]},           # self-intersecting bow tie
           {"polygon": [[0, 0], [0.5, 0], [0.5, 0.5]]},                 # 0.125 m2
           {"polygon": [[0, 0], [5000, 0], [5000, 1], [0, 1]]},         # 5 km long
           {"polygon": [[0, 0], [1, "a"], [2, 2]]},
           {"type": "school"}, {"type": "speed"},                       # unknown type; speed without limit_kmh
           {"type": "no_u_turn", "grace_s": 10},                        # grace_s only for stopping zones
           {"scene": "Atlantis"}]
    for kw in bad:
        assert zone(client, auth, **kw).status_code == 422, kw
    # closed ring and a GeoJSON geometry are both fine
    assert zone(client, auth, polygon=BOX + [BOX[0]]).status_code == 201
    g = {"type": "Polygon", "coordinates": [BOX + [BOX[0]]]}
    r = zone(client, auth, polygon=None, geometry=g, type="speed", limit_kmh=20, grace_s=None)
    assert r.status_code == 201 and r.json()["properties"]["limit_kmh"] == 20


def test_update_versions_and_deactivate(client, auth):
    zid = zone(client, auth).json()["id"]
    r = client.put(f"/api/zones/{zid}", headers=auth("planner"),
                   json={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]], "grace_s": None, "note": "moved"})
    assert r.status_code == 200, r.text
    p = r.json()["properties"]
    assert p["version"] == 2 and "grace_s" not in p and p["area_m2"] == 100.0 and p["name"] == "bank front"
    r = client.put(f"/api/zones/{zid}", headers=auth("planner"), json={"type": "speed", "limit_kmh": 30})
    assert r.json()["properties"]["type"] == "speed" and r.json()["properties"]["version"] == 3
    assert client.put(f"/api/zones/{zid}", headers=auth("planner"), json={"active": False}).status_code == 403
    r = client.delete(f"/api/zones/{zid}", headers=auth("admin"))
    assert r.status_code == 200 and not r.json()["properties"]["active"]
    assert client.delete(f"/api/zones/{zid}", headers=auth("admin")).status_code == 409
    assert client.put(f"/api/zones/{zid}", headers=auth("planner"), json={"name": "y"}).status_code == 409
    listed = client.get("/api/zones", headers=auth("officer"), params={"scene": "Town05"}).json()["features"]
    assert zid not in [f["id"] for f in listed]
    assert zid in [f["id"] for f in client.get("/api/zones", headers=auth("officer"),
                                               params={"scene": "Town05", "active": "false"}).json()["features"]]
    hist = client.get(f"/api/zones/{zid}/history", headers=auth("officer")).json()
    assert [(h["version"], h["action"], h["by"]) for h in hist] == [
        (4, "deactivate", "admin"), (3, "update", "planner"), (2, "update", "planner"), (1, "create", "planner")]
    assert hist[2]["note"] == "moved" and hist[3]["zone"]["properties"]["grace_s"] == 20
    r = client.put(f"/api/zones/{zid}", headers=auth("admin"), json={"active": True})
    assert r.status_code == 200 and r.json()["properties"]["active"] and r.json()["properties"]["version"] == 5


def test_static_zones_and_audit(client, auth):
    fc = client.get("/api/zones", headers=auth("officer"), params={"scene": "uit_congkhuA_22_3", "include_static": True}).json()
    static = [f for f in fc["features"] if f["properties"]["source"] == "scene_file"]
    assert static and all(not f["properties"]["editable"] for f in static)
    items = client.get("/api/audit", headers=auth("admin"), params={"action": "zone.*"}).json()["items"]
    assert {"zone.create", "zone.update", "zone.deactivate", "zone.history"} <= {i["action"] for i in items}


def test_engine_uses_api_zones(client, auth):
    """The zone file is the --zones shape: a profile can point road.zones at it, and the engine flags in the zone."""
    r = client.post("/api/zones", headers=auth("planner"),
                    json={"scene": "Town04", "name": "engine check", "type": "no_parking", "polygon": BOX, "grace_s": 15})
    zid, file = r.json()["id"], r.json()["file"]
    doc = client.get("/api/zones", headers=auth("officer"), params={"scene": "Town04", "format": "engine"}).json()
    disk = json.loads((config.REPO / file).read_text(encoding="utf-8"))  # absolute path in tests (temp ZONE_OUT_DIR)
    assert [z["id"] for z in doc["zones"]] == [z["id"] for z in disk["zones"]] == [zid]
    assert disk["zones"][0]["polygon"] == BOX and disk["zones"][0]["grace_s"] == 15
    prof = client.get("/api/profiles/town05", headers=auth("planner")).json()
    prof = {**prof, "name": "zones_check", "road": {**prof.get("road", {}), "zones": file}}
    r = client.put("/api/profiles/zones_check", headers=auth("planner"), json={"profile": prof, "note": "API zones"})
    assert r.status_code == 200, r.text

    sys.path.insert(0, str(ENGINE_TESTS))
    from lane_map import SceneMap
    from rules import Engine
    from run_violations import merge_zones
    from test_planner_profile import scene
    from test_rules import track
    rows = track((lambda t: (30.0, 0.0), 30.0), tid=2)  # stopped 30 s inside the API zone (grace 15 s)
    assert not [e for e in Engine(SceneMap(scene())).run(rows) if e.type == "no_parking"]
    ev = [e for e in Engine(SceneMap(merge_zones(scene(), config.REPO / file))).run(rows) if e.type == "no_parking"]
    assert ev and ev[0].zone_id == zid
    # deactivating drops it from the file
    client.delete(f"/api/zones/{zid}", headers=auth("admin"))
    assert json.loads((config.REPO / file).read_text(encoding="utf-8"))["zones"] == []
