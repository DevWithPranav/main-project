"""Zones (PRD 13.1, 14.1, 18.2): zone polygons in PostGIS, one scene / site each, versioned with author.

Types and params are the violation engine's (lane_map.py, rules.py): no_parking, crosswalk, highway
(stopping rules; optional grace_s overrides the rule's min_s), speed (limit_kmh, required), no_u_turn.
A no-stopping zone is a no_parking zone with a short grace_s. Coordinates are the scene's map metres
(CARLA world / site frame, SRID 0), like event x, y.

How the engine gets them: every change rewrites config.ZONE_OUT_DIR/<scene>.json with the scene's
active zones in the `--zones` file shape ({"zones": [{id, type, polygon, grace_s?, limit_kmh?}]}), so
`run_violations.py --zones <file>`, or a profile with `road.zones: <file>` (run_violations.py and the
live pipeline read it), runs with them. `GET /zones?scene=&format=engine` returns the same document.
"""

import json
import math
import re
import uuid
from datetime import datetime, timezone
from functools import cache

import shapely
from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config, engine_bridge  # noqa: F401 - engine_bridge puts the engine on sys.path
from ..auth import Principal, current_user, require
from ..db import get_db
from ..events import iso
from ..models import Zone, ZoneVersion

router = APIRouter()
TYPES = ("no_parking", "crosswalk", "highway", "speed", "no_u_turn")
STOP_TYPES = ("no_parking", "crosswalk", "highway")  # StopInZoneMonitor zones: grace_s applies
MIN_AREA_M2, MAX_AREA_M2, MAX_SPAN_M, MAX_VERTICES = 1.0, 1_000_000.0, 2000.0, 500


@cache
def scopes() -> dict[str, str]:
    """lower-case name -> name of every scene (lane map) and site the engine has."""
    names = [p.stem for p in config.SCENE_DIR.glob("*.json") if not p.stem.endswith("_objects")]
    names += [p.stem for p in config.SITE_DIR.glob("*.json") if not p.stem.endswith(".scene")]
    return {n.lower(): n for n in names}


def scope_of(scene: str) -> str:
    s = scopes().get(scene.lower())
    if s is None:
        raise HTTPException(422, f"unknown scene {scene!r} (scenes and sites: {', '.join(sorted(scopes().values()))})")
    return s


def ring_of(polygon: list | None, geometry: dict | None) -> list[list[float]]:
    if geometry is not None:
        if geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
            raise HTTPException(422, "geometry must be a GeoJSON Polygon")
        if len(geometry["coordinates"]) > 1:
            raise HTTPException(422, "zone polygons have no holes")
        polygon = geometry["coordinates"][0]
    if polygon is None:
        raise HTTPException(422, "polygon (or geometry) is required")
    return polygon


def check_polygon(pts: list) -> shapely.Polygon:
    """A simple polygon of sane size, in map metres; an open ring is closed, a closed one kept."""
    try:
        pts = [(float(p[0]), float(p[1])) for p in pts]  # a GeoJSON position may carry z: ignored
    except (TypeError, ValueError, IndexError):
        pts = []
    if not pts or not all(math.isfinite(v) and abs(v) < 1e6 for p in pts for v in p):
        raise HTTPException(422, "polygon must be a list of [x, y] map-metre pairs")
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(set(pts)) < 3:
        raise HTTPException(422, "polygon needs at least 3 distinct vertices")
    if len(pts) > MAX_VERTICES:
        raise HTTPException(422, f"polygon has more than {MAX_VERTICES} vertices")
    poly = shapely.Polygon(pts)
    if not poly.is_valid:
        raise HTTPException(422, f"polygon is not simple: {shapely.is_valid_reason(poly)}")
    x0, y0, x1, y1 = poly.bounds
    if not MIN_AREA_M2 <= poly.area <= MAX_AREA_M2 or max(x1 - x0, y1 - y0) > MAX_SPAN_M:
        raise HTTPException(422, f"polygon area {poly.area:.1f} m2 / span {max(x1 - x0, y1 - y0):.0f} m out of range "
                                 f"({MIN_AREA_M2:g}-{MAX_AREA_M2:g} m2, span <= {MAX_SPAN_M:g} m)")
    return poly


def check_params(ztype: str, params: dict) -> dict:
    if ztype not in TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(TYPES)}")
    out = {k: v for k, v in params.items() if v is not None}
    if "grace_s" in out and (ztype not in STOP_TYPES or not 0 <= out["grace_s"] <= 3600):
        raise HTTPException(422, f"grace_s is 0-3600 s and only for {', '.join(STOP_TYPES)} zones")
    if ztype == "speed" and not 1 <= out.get("limit_kmh", 0) <= 200:
        raise HTTPException(422, "a speed zone needs limit_kmh (1-200)")
    if ztype != "speed":
        out.pop("limit_kmh", None)
    return out


def polygon_of(z: Zone) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in to_shape(z.geom).exterior.coords[:-1]]


def to_api(z: Zone) -> dict:
    """The zone as a GeoJSON Feature (closed ring), its properties flat."""
    pts = polygon_of(z)
    return {"type": "Feature", "id": z.id, "geometry": {"type": "Polygon", "coordinates": [pts + pts[:1]]},
            "properties": {"id": z.id, "scene": z.scene, "name": z.name, "type": z.zone_type, **z.params,
                           "active": z.active, "version": z.version, "area_m2": round(to_shape(z.geom).area, 1),
                           "created_by": z.created_by, "created_at": iso(z.created_at),
                           "updated_by": z.updated_by, "updated_at": iso(z.updated_at), "source": "api"}}


def to_engine(z: Zone) -> dict:
    """The zone as lane_map.py reads it (the `--zones` file / profile road.extra_zones item shape)."""
    return {"id": z.id, "type": z.zone_type, "polygon": polygon_of(z), **z.params, "name": z.name,
            "source": "api", "version": z.version}


def static_zones(scene: str) -> list[dict]:
    """Zones the scene's own lane map carries, or a site file's (drawn in pixels, zone_tool.py: converted to
    site metres by real_geometry.Site). Read-only here."""
    p = config.SCENE_DIR / f"{scene}.json"
    try:
        if p.exists():
            zones = json.loads(p.read_text(encoding="utf-8")).get("zones", [])
        else:
            from real_geometry import Site  # engine module; needs cv2
            zones = Site(config.SITE_DIR / f"{scene}.json").scene()["zones"]
    except Exception:  # noqa: BLE001 - e.g. a site whose calibration file is missing
        return []
    out = []
    for z in zones:
        pts = [list(map(float, q[:2])) for q in z["polygon"]]
        props = {k: v for k, v in z.items() if k != "polygon"}
        out.append({"type": "Feature", "id": z["id"], "geometry": {"type": "Polygon", "coordinates": [pts + pts[:1]]},
                    "properties": {**props, "scene": scene, "active": True, "source": "scene_file", "editable": False}})
    return out


async def active_zones(db: AsyncSession, scene: str) -> list[Zone]:
    return list((await db.execute(select(Zone).where(Zone.scene == scene, Zone.active.is_(True))
                                  .order_by(Zone.created_at, Zone.id))).scalars())


def engine_doc(scene: str, zones: list[Zone]) -> dict:
    return {"scene": scene, "source": "backend /api/zones", "generated_at": iso(datetime.now(timezone.utc)),
            "zones": [to_engine(z) for z in zones]}


async def write_zone_file(db: AsyncSession, scene: str) -> str | None:
    """Rewrite <ZONE_OUT_DIR>/<scene>.json; returns its path (repo-relative when inside the repo)."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", scene) or scene.startswith("."):
        return None
    config.ZONE_OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.ZONE_OUT_DIR / f"{scene}.json"
    path.write_text(json.dumps(engine_doc(scene, await active_zones(db, scene)), indent=1), encoding="utf-8")
    return path.relative_to(config.REPO).as_posix() if config.REPO in path.parents else str(path)


async def save(db: AsyncSession, z: Zone, action: str, user: Principal, note: str | None) -> dict:
    await db.flush()
    db.add(ZoneVersion(zone_id=z.id, version=z.version, action=action, data=to_api(z), by=user.username,
                       at=z.updated_at, note=note))
    await db.commit()
    return {**to_api(z), "file": await write_zone_file(db, z.scene)}


async def get_zone(db: AsyncSession, zone_id: str) -> Zone:
    z = await db.get(Zone, zone_id)
    if z is None:
        raise HTTPException(404, f"no zone {zone_id}")
    return z


@router.get("/zones")
async def list_zones(scene: str | None = None, type: str | None = None,
                     active: str = Query("true", pattern="^(true|false|all)$"),
                     format: str = Query("geojson", pattern="^(geojson|engine)$"),
                     include_static: bool = Query(False, description="also the scene file's own zones (read-only)"),
                     db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    """GeoJSON FeatureCollection of the zones; format=engine (needs scene): the --zones file document."""
    sc = scope_of(scene) if scene else None
    if format == "engine":
        if sc is None:
            raise HTTPException(422, "format=engine needs scene")
        return engine_doc(sc, await active_zones(db, sc))
    q = select(Zone).order_by(Zone.scene, Zone.created_at, Zone.id)
    if sc:
        q = q.where(Zone.scene == sc)
    if type:
        q = q.where(Zone.zone_type.in_([t.strip() for t in type.split(",") if t.strip()]))
    if active != "all":
        q = q.where(Zone.active.is_(active == "true"))
    feats = [to_api(z) for z in (await db.execute(q)).scalars()]
    if include_static and sc:
        feats += [f for f in static_zones(sc) if not type or f["properties"].get("type") in type.split(",")]
    return {"type": "FeatureCollection", "crs_note": "coordinates are map metres (CARLA world / site frame, SRID 0)",
            "features": feats}


class ZoneIn(BaseModel):
    scene: str
    name: str = Field(min_length=1, max_length=256)
    type: str
    polygon: list[list[float]] | None = None  # [[x, y], ...] map metres; or a GeoJSON Polygon in geometry
    geometry: dict | None = None
    grace_s: float | None = None
    limit_kmh: float | None = None
    note: str | None = None


@router.post("/zones", status_code=201)
async def create_zone(body: ZoneIn, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    sc = scope_of(body.scene)
    poly = check_polygon(ring_of(body.polygon, body.geometry))
    params = check_params(body.type, {"grace_s": body.grace_s, "limit_kmh": body.limit_kmh})
    now = datetime.now(timezone.utc)
    z = Zone(id=f"z_{uuid.uuid4().hex[:12]}", scene=sc, name=body.name, zone_type=body.type, geom=from_shape(poly, srid=0),
             params=params, active=True, version=1, created_by=user.username, created_at=now,
             updated_by=user.username, updated_at=now)
    db.add(z)
    return await save(db, z, "create", user, body.note)


@router.get("/zones/{zone_id}")
async def read_zone(zone_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    return to_api(await get_zone(db, zone_id))


class ZonePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=256)
    type: str | None = None
    polygon: list[list[float]] | None = None
    geometry: dict | None = None
    grace_s: float | None = None  # sent as null: cleared (the rule's default applies)
    limit_kmh: float | None = None
    active: bool | None = None  # true reactivates a deleted zone (ADMIN)
    note: str | None = None


@router.put("/zones/{zone_id}")
async def update_zone(zone_id: str, body: ZonePatch, db: AsyncSession = Depends(get_db),
                      user: Principal = Depends(require("PLANNER"))):
    """Change geometry and / or metadata of a zone; only the fields sent change. A new version."""
    z = await get_zone(db, zone_id)
    sent = body.model_fields_set
    if body.active is not None and body.active != z.active and user.role != "ADMIN":
        raise HTTPException(403, "only ADMIN may activate or deactivate a zone")
    if not z.active and body.active is not True:
        raise HTTPException(409, f"zone {zone_id} is inactive (ADMIN can send active: true)")
    if body.polygon is not None or body.geometry is not None:
        z.geom = from_shape(check_polygon(ring_of(body.polygon, body.geometry)), srid=0)
    ztype = body.type or z.zone_type
    keep = z.params if ztype == z.zone_type else {}  # a new type starts from the rule defaults
    params = {**keep, **{k: getattr(body, k) for k in ("grace_s", "limit_kmh") if k in sent}}
    z.params, z.zone_type = check_params(ztype, params), ztype
    if body.name:
        z.name = body.name
    action = "reactivate" if body.active and not z.active else "update"
    z.active = True
    z.version += 1
    z.updated_by, z.updated_at = user.username, datetime.now(timezone.utc)
    return await save(db, z, action, user, body.note)


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str, note: str | None = None, db: AsyncSession = Depends(get_db),
                      user: Principal = Depends(require())):
    """Deactivate (PRD 14.1: ADMIN): the zone and its history stay; the engine file drops it."""
    z = await get_zone(db, zone_id)
    if not z.active:
        raise HTTPException(409, f"zone {zone_id} is already inactive")
    z.active, z.version = False, z.version + 1
    z.updated_by, z.updated_at = user.username, datetime.now(timezone.utc)
    return await save(db, z, "deactivate", user, note)


@router.get("/zones/{zone_id}/history")
async def zone_history(zone_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    await get_zone(db, zone_id)
    rows = (await db.execute(select(ZoneVersion).where(ZoneVersion.zone_id == zone_id)
                             .order_by(ZoneVersion.version.desc()))).scalars()
    return [{"version": r.version, "action": r.action, "by": r.by, "at": iso(r.at), "note": r.note, "zone": r.data}
            for r in rows]
