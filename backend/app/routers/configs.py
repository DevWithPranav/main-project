"""Configuration: profiles (versioned), conditions, scenes (lane maps)."""

import json
from datetime import datetime, timezone
from functools import cache

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..auth import Principal, current_user, require
from ..db import get_db
from ..engine_bridge import load, profile_errors
from ..events import iso
from ..models import Profile

router = APIRouter()


async def seed_profiles(db: AsyncSession) -> None:
    have = set((await db.execute(select(Profile.name).distinct())).scalars())
    for p in sorted(config.PROFILE_DIR.glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        name = doc.get("name", p.stem)
        if name not in have:
            db.add(Profile(name=name, version=1, data=doc, by="seed", at=datetime.now(timezone.utc),
                           note=f"seeded from {p.relative_to(config.REPO).as_posix()}"))
    await db.commit()


async def latest(db: AsyncSession, name: str) -> Profile | None:
    return (await db.execute(select(Profile).where(Profile.name == name).order_by(Profile.version.desc())
                             .limit(1))).scalar_one_or_none()


def meta(p: Profile) -> dict:
    return {"name": p.name, "version": p.version, "by": p.by, "at": iso(p.at), "note": p.note}


@router.get("/profiles")
async def list_profiles(db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    """Latest version of every profile, as the profile documents themselves."""
    top = select(Profile.name, func.max(Profile.version).label("v")).group_by(Profile.name).subquery()
    rows = (await db.execute(select(Profile).join(top, (Profile.name == top.c.name) & (Profile.version == top.c.v))
                             .order_by(Profile.name))).scalars()
    return [r.data for r in rows]


@router.get("/profiles/{name}")
async def get_profile(name: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    p = await latest(db, name)
    if p is None:
        raise HTTPException(404, f"no profile {name}")
    return p.data


class ProfilePut(BaseModel):
    profile: dict
    note: str | None = None


@router.put("/profiles/{name}")
async def put_profile(name: str, body: ProfilePut, db: AsyncSession = Depends(get_db),
                      user: Principal = Depends(require("PLANNER"))):
    doc = body.profile
    if doc.get("name", name) != name:
        raise HTTPException(422, f"profile name {doc.get('name')!r} does not match the URL ({name!r})")
    doc = {**doc, "name": name}
    errs = profile_errors(doc)
    if errs:
        raise HTTPException(422, "; ".join(errs[:20]))
    prev = await latest(db, name)
    p = Profile(name=name, version=(prev.version + 1) if prev else 1, data=doc, by=user.username,
                at=datetime.now(timezone.utc), note=body.note)
    db.add(p)
    await db.commit()
    return meta(p)


@router.get("/profiles/{name}/history")
async def profile_history(name: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    rows = list((await db.execute(select(Profile).where(Profile.name == name).order_by(Profile.version.desc()))).scalars())
    if not rows:
        raise HTTPException(404, f"no profile {name}")
    return [{**meta(r), "profile": r.data} for r in rows]


@router.get("/conditions")
async def get_conditions(_: Principal = Depends(current_user)):
    return load("conditions.json")


@cache
def scene_summary(path_str: str, mtime: float) -> dict:
    from pathlib import Path
    d = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {"town": Path(path_str).stem, "scene": d.get("scene"), "coords": d.get("coords"),
            "source": d.get("source"), "n_lanes": len(d.get("lanes", [])), "n_zones": len(d.get("zones", []))}


@router.get("/scenes")
async def list_scenes(_: Principal = Depends(current_user)):
    return [scene_summary(str(p), p.stat().st_mtime) for p in sorted(config.SCENE_DIR.glob("*.json"))
            if not p.stem.endswith("_objects")]


@router.get("/scenes/{town}/objects")
async def get_scene_objects(town: str, _: Principal = Depends(current_user)):
    """Static town objects as oriented boxes (export_town_objects.py), for the 3D twin."""
    p = config.SCENE_DIR / f"{town}_objects.json"
    if not p.exists():
        raise HTTPException(404, f"no objects for {town} (run simulation/carla_scripts/export_town_objects.py {town})")
    return FileResponse(p, media_type="application/json")


@router.get("/scenes/{town}")
async def get_scene(town: str, _: Principal = Depends(current_user)):
    for p in config.SCENE_DIR.glob("*.json"):
        if p.stem.lower() == town.lower():
            return FileResponse(p, media_type="application/json")
    raise HTTPException(404, f"no scene {town}")
