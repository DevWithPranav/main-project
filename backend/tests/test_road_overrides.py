"""A profile's road.lane_overrides on the lane map (GET /api/scenes/{town}?profile=) and the editable
attribute list (GET /api/road/attributes): every road attribute the engine reads, per lane / road."""

PROFILE = "road_override_check_api"
OVERRIDES = [{"lane_id": "r46_*", "road_class": "highway", "note": "planner: motorway"},
             {"lane_id": "r46_s0_l2", "lane_change": "none", "bridge": True, "speed_limit_kmh": 30},
             {"lane_id": "r46_s0_l-1", "restricted": "bus", "one_way": True, "ramp": "on", "median_left": True,
              "tunnel": True, "lane_type": "shoulder"}]


def put(client, auth, overrides, name=PROFILE):
    doc = {"profile_version": 1, "name": name, "applies_to": {"map": "Town05"}, "road": {"lane_overrides": overrides}}
    return client.put(f"/api/profiles/{name}", headers=auth("planner"), json={"profile": doc, "note": "test"})


def test_scene_with_profile_overrides(client, auth):
    assert put(client, auth, OVERRIDES).status_code == 200
    h = auth("officer")
    plain = {l["id"]: l for l in client.get("/api/scenes/Town05", headers=h).json()["lanes"]}
    r = client.get(f"/api/scenes/town05?profile={PROFILE}", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    lanes = {l["id"]: l for l in d["lanes"]}
    assert d["profile"] == PROFILE and set(lanes) == set(plain)
    r46 = [i for i in lanes if i.startswith("r46_")]
    assert d["overrides_applied"] == len(r46) == sum("overridden" in l for l in d["lanes"])
    a, b = lanes["r46_s0_l2"], lanes["r46_s0_l-1"]
    assert (a["road_class"], a["lane_change"], a["bridge"], a["speed_limit_kmh"]) == ("highway", "none", True, 30)
    assert a["overridden"] == ["bridge", "lane_change", "road_class"]  # 30 km/h was already the limit
    assert (b["restricted"], b["one_way"], b["ramp"], b["median_left"], b["tunnel"], b["lane_type"]) == \
        ("bus", True, "on", True, True, "shoulder")
    assert "note" not in a and "overridden" not in plain["r46_s0_l2"]
    assert all(lanes[i]["road_class"] == "highway" for i in r46)
    assert all(lanes[i] == plain[i] for i in plain if not i.startswith("r46_"))


def test_scene_profile_errors(client, auth):
    h = auth("officer")
    assert client.get("/api/scenes/Town05?profile=no_such_profile", headers=h).status_code == 404
    assert put(client, auth, [{"lane_id": "r99999_*", "bridge": True}], name=PROFILE + "_typo").status_code == 200
    r = client.get(f"/api/scenes/Town05?profile={PROFILE}_typo", headers=h)
    assert r.status_code == 422 and "matches no lane" in r.json()["detail"]


def test_profile_put_validates_override_items(client, auth):
    for bad in ({"lane_id": "r46_*", "road_class": "motorway"}, {"lane_id": "r46_*", "bridge": "yes"},
                {"lane_id": "r46_*", "width_m": 3}, {"speed_limit_kmh": 30}):
        r = put(client, auth, [bad], name=PROFILE + "_bad")
        assert r.status_code == 422, bad


def test_road_attributes(client, auth):
    r = client.get("/api/road/attributes", headers=auth("officer"))
    assert r.status_code == 200
    at = {a["key"]: a for a in r.json()}
    assert list(at) == ["speed_limit_kmh", "road_class", "lane_type", "lane_change", "restricted", "one_way",
                        "bridge", "tunnel", "ramp", "median_left"]
    assert {a["type"] for a in at.values()} == {"enum", "bool", "number"}
    assert at["ramp"]["values"] == ["on", "off", "link", None]
    assert {d["condition"] for d in at["bridge"]["drives"]} == {"B5"}
    assert at["lane_change"]["drives"] == [{"condition": "A3", "label": "Illegal lane change"}]
    assert all(d["label"] for a in at.values() for d in a["drives"])
