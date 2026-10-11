"""Traffic plans: the scenario builder's API (Scenario_Builder_Plan.md, ml/planning/plans.py).

    GET  /plans/catalogue                kinds, parameters, replay / CARLA support, templates
    GET  /plans/locate                   what is at a map position (nearest lanes, junction, zones, plan items)
    POST /plans/preview                  validation + conflicts + compile summary + risks + alternatives of a draft, at once
    POST /plans                          save a plan (version 1)             PLANNER
    PUT  /plans/{id}                     save a new version                  PLANNER (the owner or ADMIN)
    GET  /plans, /plans/{id}[/versions]  list / latest / history
    POST /plans/analyze                  background analysis of a draft or a saved version on a session's recorded
                                         traffic (observed baseline + violation-engine replay)  PLANNER
    GET  /plans/runs/{id}                the run (status running | done | failed) and its result

Numbers in a run are labelled observed / replay (measured) / rule of thumb; metrics that need a CARLA run are listed
as pending, never estimated.
"""

import asyncio
import importlib
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, current_user, require
from ..db import SessionLocal, get_db
from ..models import Plan, PlanReport, PlanRun, PlanVersion, Session
from .configs import scene_doc, scene_path
from .scenarios import violations_dir

router = APIRouter()
log = logging.getLogger("backend.plans")
_tasks: set[asyncio.Task] = set()
_progress: dict[str, dict] = {}  # run id -> {stage, done, total} while it runs (in memory; gone after a restart)


def reporter(run_id: str):
    def note(stage: str, done: int, total: int) -> None:
        _progress[run_id] = {"stage": stage, "done": done, "total": total}
    return note


def mod(name: str):
    return importlib.import_module(f"ml.planning.{name}")


def scene_of(town: str) -> dict:
    p = scene_path(town)
    return scene_doc(str(p), p.stat().st_mtime)


class PlanDoc(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = ""
    scene: str
    session_id: str | None = None
    objectives: list[str] = Field(default_factory=list)
    props: list[dict] = Field(default_factory=list)
    rules: list[dict] = Field(default_factory=list)
    scenarios: list[dict] = Field(default_factory=list)
    traffic: dict | None = None  # custom traffic configuration (ml/planning/traffic_config.py); None = defaults


class SaveReq(BaseModel):
    plan: PlanDoc
    note: str | None = Field(default=None, max_length=1000)


class AnalyzeReq(BaseModel):
    plan_id: str | None = None
    version: int | None = None
    plan: PlanDoc | None = None  # an unsaved draft
    session_id: str | None = None


def row_out(p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "scene": p.scene, "owner": p.owner, "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(), "latest_version": p.latest_version, "archived": p.archived}


def run_out(r: PlanRun, full: bool = True) -> dict:
    d = {"id": r.id, "plan_id": r.plan_id, "plan_version": r.plan_version, "session_id": r.session_id, "kind": r.kind, "by": r.by,
         "at": r.at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None, "status": r.status, "error": r.error}
    if r.status == "running":
        d["progress"] = _progress.get(r.id)
    if full:
        d["plan"] = r.request
        d["result"] = r.result
    else:
        res = r.result or {}
        d["evidence"] = (res.get("evidence") or {}).get("level")
        d["replay_counted"] = [res["replay"]["baseline"]["counted"], res["replay"]["modified"]["counted"]] if "replay" in res else None
    return d


@router.get("/plans/catalogue")
async def catalogue(_: Principal = Depends(current_user)):
    return mod("plan_catalogue").catalogue()


@router.get("/plans/traffic/schema")
async def traffic_schema(_: Principal = Depends(current_user)):
    """The custom-traffic settings: sections and fields with ranges, defaults, presets, demand profiles, driver behaviours, source mixes."""
    return mod("traffic_config").schema_doc()


@router.get("/plans/locate")
async def locate(scene: str, x: float, y: float, radius: float = 40.0, _: Principal = Depends(current_user)):
    if not 5 <= radius <= 200:
        raise HTTPException(422, "radius must be 5-200 m")
    return mod("plans").locate(scene_of(scene), x, y, radius)


@router.post("/plans/preview")
async def preview(body: PlanDoc, _: Principal = Depends(current_user)):
    """Immediate feedback on a draft (no recorded traffic needed): validation, what the replay can judge, risks, alternatives."""
    plans = mod("plans")
    return await asyncio.to_thread(plans.analyze, body.model_dump(), scene_of(body.scene), None)


async def latest_doc(db: AsyncSession, plan_id: str, version: int | None = None) -> tuple[Plan, PlanVersion]:
    p = await db.get(Plan, plan_id)
    if p is None:
        raise HTTPException(404, f"no plan {plan_id}")
    v = version or p.latest_version
    pv = (await db.execute(select(PlanVersion).where(PlanVersion.plan_id == plan_id, PlanVersion.version == v))).scalar_one_or_none()
    if pv is None:
        raise HTTPException(404, f"plan {plan_id} has no version {v}")
    return p, pv


def check_scene(doc: PlanDoc) -> None:
    scene_path(doc.scene)  # 404 for an unknown town


@router.post("/plans", status_code=201)
async def create(body: SaveReq, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    check_scene(body.plan)
    now = datetime.now(timezone.utc)
    p = Plan(id="plan-" + uuid.uuid4().hex[:12], name=body.plan.name, scene=body.plan.scene, owner=user.username,
             created_at=now, updated_at=now, latest_version=1)
    db.add(p)
    await db.flush()
    db.add(PlanVersion(plan_id=p.id, version=1, doc=body.plan.model_dump(), by=user.username, at=now, note=body.note))
    await db.commit()
    return {**row_out(p), "plan": body.plan.model_dump()}


@router.put("/plans/{plan_id}")
async def save_version(plan_id: str, body: SaveReq, base_version: int | None = None, db: AsyncSession = Depends(get_db),
                       user: Principal = Depends(require("PLANNER"))):
    p = await db.get(Plan, plan_id)
    if p is None:
        raise HTTPException(404, f"no plan {plan_id}")
    if user.role != "ADMIN" and p.owner != user.username:
        raise HTTPException(403, "only the owner or an admin may change this plan (copy it to work on your own)")
    if base_version is not None and base_version != p.latest_version:
        raise HTTPException(409, f"the plan is at version {p.latest_version}, you edited version {base_version}")
    check_scene(body.plan)
    p.latest_version += 1
    p.name, p.scene, p.updated_at = body.plan.name, body.plan.scene, datetime.now(timezone.utc)
    db.add(PlanVersion(plan_id=p.id, version=p.latest_version, doc=body.plan.model_dump(), by=user.username, at=p.updated_at, note=body.note))
    await db.commit()
    return {**row_out(p), "plan": body.plan.model_dump()}


@router.get("/plans")
async def list_(scene: str | None = None, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    q = select(Plan).where(Plan.archived.is_(False)).order_by(Plan.updated_at.desc())
    if scene:
        q = q.where(Plan.scene == scene)
    return [row_out(p) for p in (await db.execute(q)).scalars()]


@router.get("/plans/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    r = await db.get(PlanRun, run_id)
    if r is None:
        raise HTTPException(404, f"no run {run_id}")
    return run_out(r)


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, version: int | None = None, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    p, pv = await latest_doc(db, plan_id, version)
    return {**row_out(p), "version": pv.version, "by": pv.by, "at": pv.at.isoformat(), "note": pv.note, "plan": pv.doc}


@router.get("/plans/{plan_id}/versions")
async def versions(plan_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    if await db.get(Plan, plan_id) is None:
        raise HTTPException(404, f"no plan {plan_id}")
    rows = (await db.execute(select(PlanVersion).where(PlanVersion.plan_id == plan_id).order_by(PlanVersion.version.desc()))).scalars()
    return [{"version": v.version, "by": v.by, "at": v.at.isoformat(), "note": v.note,
             "items": {k: len(v.doc.get(k) or []) for k in ("props", "rules", "scenarios")}} for v in rows]


@router.get("/plans/{plan_id}/runs")
async def runs(plan_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    rows = (await db.execute(select(PlanRun).where(PlanRun.plan_id == plan_id).order_by(PlanRun.at.desc()))).scalars()
    return [run_out(r, full=False) for r in rows]


@router.delete("/plans/{plan_id}", status_code=204)
async def archive(plan_id: str, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    p = await db.get(Plan, plan_id)
    if p is None:
        raise HTTPException(404, f"no plan {plan_id}")
    if user.role != "ADMIN" and p.owner != user.username:
        raise HTTPException(403, "only the owner or an admin may delete this plan")
    p.archived = True  # kept, with its versions and runs
    await db.commit()


@router.post("/plans/analyze", status_code=202)
async def analyze(body: AnalyzeReq, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    version = None
    if body.plan_id:
        p, pv = await latest_doc(db, body.plan_id, body.version)
        doc, version = pv.doc, pv.version
    elif body.plan is not None:
        doc = body.plan.model_dump()
    else:
        raise HTTPException(422, "give plan_id or a draft plan")
    sid = body.session_id or doc.get("session_id")
    vdir = None
    if sid:
        s = await db.get(Session, sid)
        if s is None:
            raise HTTPException(404, f"no session {sid}")
        vdir = violations_dir(s)
        if vdir is None:
            raise HTTPException(422, "this session has no recorded kinematics to replay (live sessions and old imports)")
    scene = scene_of(doc["scene"])
    run = PlanRun(id="run-" + uuid.uuid4().hex[:12], plan_id=body.plan_id, plan_version=version, session_id=sid, kind="replay",
                  by=user.username, at=datetime.now(timezone.utc), status="running", request=doc)
    db.add(run)
    await db.commit()
    plans = mod("plans")

    async def work() -> None:
        try:
            res = await asyncio.to_thread(plans.analyze, doc, scene, vdir, reporter(run.id))
            status, err = "done", None
        except Exception as e:  # noqa: BLE001 - shown to the planner
            log.warning("plan run %s failed: %s", run.id, e)
            res, status, err = None, "failed", str(e)
        async with SessionLocal() as db2:
            r = await db2.get(PlanRun, run.id)
            r.status, r.result, r.error, r.finished_at = status, res, err, datetime.now(timezone.utc)
            await db2.commit()
        _progress.pop(run.id, None)

    t = asyncio.create_task(work())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return run_out(run, full=False)


# ---------------------------------------------------------------------------- reports

REPORT_MEDIA = {"pdf": ("application/pdf", "pdf"), "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
                "csv": ("text/csv; charset=utf-8", "csv")}
MAX_SHOTS, MAX_SHOT_BYTES = 6, 4_000_000


class ReportReq(BaseModel):
    plan: PlanDoc
    plan_id: str | None = None
    version: int | None = None
    analysis: dict | None = None  # the analysis the planner is looking at (preview or a run's result)
    comparison: list[dict] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list, description="PNG data URLs of the digital twin")
    format: str = Field(default="pdf", pattern="^(pdf|xlsx|csv)$")
    session_id: str | None = None
    analysed_at: str | None = None
    virtual: dict | None = None  # the virtual-simulation result shown (without the vehicle paths)


def report_out(r: PlanReport) -> dict:
    return {"id": r.id, "plan_id": r.plan_id, "plan_version": r.plan_version, "title": r.title, "format": r.format, "filename": r.filename,
            "size_bytes": r.size_bytes, "evidence": r.evidence, "session_id": r.session_id, "by": r.by, "at": r.at.isoformat(),
            "stored": r.storage_key is not None, "download_url": f"/api/plans/reports/{r.id}/file"}


def decode_shots(urls: list[str]) -> list[bytes]:
    import base64
    out = []
    for u in urls[:MAX_SHOTS]:
        head, _, b64 = u.partition(",")
        if not head.startswith("data:image/png") or len(b64) > MAX_SHOT_BYTES * 4 // 3:
            raise HTTPException(422, "screenshots must be PNG data URLs of at most 4 MB each")
        try:
            out.append(base64.b64decode(b64, validate=True))
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "a screenshot is not valid base64") from None
    return out


@router.post("/plans/report", status_code=201)
async def make_report(body: ReportReq, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    """Build a planning report of the plan (and the analysis shown), keep it in S3 and return its record; download from download_url."""
    import time

    from .. import plan_report, storage
    t0 = time.perf_counter()
    plan = body.plan.model_dump()
    if body.plan_id and await db.get(Plan, body.plan_id) is None:
        raise HTTPException(404, f"no plan {body.plan_id}")
    analysis = body.analysis
    if analysis is None:  # no analysis given: at least the immediate checks
        analysis = await asyncio.to_thread(mod("plans").analyze, plan, scene_of(plan["scene"]), None)
    ctx = plan_report.make_ctx(plan, analysis, body.comparison, decode_shots(body.screenshots), user.username,
                               kinds=mod("plan_catalogue").catalogue()["kinds"], plan_id=body.plan_id, version=body.version,
                               session_id=body.session_id, analysed_at=body.analysed_at, virtual=body.virtual)
    writer = {"pdf": plan_report.to_pdf, "xlsx": plan_report.to_xlsx, "csv": plan_report.to_csv}[body.format]
    data = await asyncio.to_thread(writer, ctx)
    media, ext = REPORT_MEDIA[body.format]
    now = datetime.now(timezone.utc)
    rid = "rep-" + uuid.uuid4().hex[:12]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (plan["name"] or "plan"))[:60]
    fname = f"plan_{safe}_{now:%Y%m%d_%H%M%S}.{ext}"
    key = f"plan-reports/{now:%Y/%m}/{rid}_{fname}"
    try:
        await asyncio.to_thread(storage.put_bytes, data, key, media)
    except Exception as e:  # noqa: BLE001 - listed but not downloadable again
        log.warning("plan report %s not stored: %s", rid, e)
        key = None
    row = PlanReport(id=rid, plan_id=body.plan_id, plan_version=body.version, title=plan["name"], format=body.format, filename=fname,
                     content_type=media, size_bytes=len(data), storage_key=key, evidence=(analysis.get("evidence") or {}).get("level"),
                     session_id=body.session_id or plan.get("session_id"), by=user.username, at=now)
    db.add(row)
    await db.commit()
    return {**report_out(row), "seconds": round(time.perf_counter() - t0, 2), "file_base64": __import__("base64").b64encode(data).decode() if key is None else None}


@router.get("/plans/reports/{report_id}/file")
async def report_file(report_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    from fastapi.responses import StreamingResponse

    from .. import config, storage
    r = await db.get(PlanReport, report_id)
    if r is None or r.storage_key is None:
        raise HTTPException(404, "no such report file")
    obj = await asyncio.to_thread(storage.client().get_object, Bucket=config.S3_BUCKET, Key=r.storage_key)
    return StreamingResponse(obj["Body"].iter_chunks(1 << 16), media_type=r.content_type,
                             headers={"Content-Disposition": f'attachment; filename="{r.filename}"'})


@router.get("/plans/{plan_id}/reports")
async def list_reports(plan_id: str, db: AsyncSession = Depends(get_db), _: Principal = Depends(current_user)):
    """Reports made of this plan (any version), newest first; each has the plan version so the scenario can be reopened from it."""
    q = select(PlanReport).where(PlanReport.plan_id == plan_id).order_by(PlanReport.at.desc()).limit(100)
    return [report_out(r) for r in (await db.execute(q)).scalars()]


# ---------------------------------------------------------------------------- virtual traffic

class SimulateReq(BaseModel):
    plan_id: str | None = None
    version: int | None = None
    plan: PlanDoc | None = None
    seeds: int = Field(default=5, ge=1, le=10)
    duration_s: float = Field(default=120.0, ge=30.0, le=600.0)


@router.post("/plans/simulate", status_code=202)
async def simulate(body: SimulateReq, db: AsyncSession = Depends(get_db), user: Principal = Depends(require("PLANNER"))):
    """Virtual traffic (ml/planning/microsim.py): the plan against the baseline on the same seeds, as a background run
    (kind 'virtual'). The result has metrics per variant (mean and 95 % interval over the seeds), the model notes, and the
    vehicle paths of seed 0 for both variants (replay format) so the twin can play them."""
    version = None
    if body.plan_id:
        _, pv = await latest_doc(db, body.plan_id, body.version)
        doc, version = pv.doc, pv.version
    elif body.plan is not None:
        doc = body.plan.model_dump()
    else:
        raise HTTPException(422, "give plan_id or a draft plan")
    if not any(doc.get(k) for k in ("props", "rules", "scenarios")):
        raise HTTPException(422, "the plan is empty: add something to simulate")
    scene = scene_of(doc["scene"])
    plans = mod("plans")
    val = await asyncio.to_thread(plans.validate, doc, scene)
    if not val["ok"]:
        raise HTTPException(422, "fix the plan first: " + "; ".join(m["message"] for m in (val["errors"] + val["conflicts"])[:3]))
    run = PlanRun(id="run-" + uuid.uuid4().hex[:12], plan_id=body.plan_id, plan_version=version, session_id=None, kind="virtual",
                  by=user.username, at=datetime.now(timezone.utc), status="running", request={**doc, "_sim": {"seeds": body.seeds, "duration_s": body.duration_s}})
    db.add(run)
    await db.commit()
    ms = mod("microsim")

    def work_sync():
        res = ms.compare(scene, doc, seeds=body.seeds, duration_s=body.duration_s, progress=reporter(run.id))
        states = res.pop("states")
        res["trajectories"] = {"baseline": ms.trajectories(states["baseline"]), "plan": ms.trajectories(states["plan"])}
        res["validation"] = val
        return res

    async def work() -> None:
        try:
            res = await asyncio.to_thread(work_sync)
            status, err = "done", None
        except Exception as e:  # noqa: BLE001
            log.warning("virtual run %s failed: %s", run.id, e)
            res, status, err = None, "failed", str(e)
        async with SessionLocal() as db2:
            r = await db2.get(PlanRun, run.id)
            r.status, r.result, r.error, r.finished_at = status, res, err, datetime.now(timezone.utc)
            await db2.commit()
        _progress.pop(run.id, None)

    t = asyncio.create_task(work())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return run_out(run, full=False)


class TrafficReq(BaseModel):
    scene: str
    plan: PlanDoc | None = None  # None / empty: plain traffic
    traffic: dict | None = None  # custom traffic configuration; wins over plan.traffic
    seed: int | None = Field(default=None, ge=0, le=999)  # default: the configuration's seed
    duration_s: float | None = Field(default=None, ge=30.0, le=300.0)


@router.post("/plans/traffic")
async def custom_traffic(body: TrafficReq, _: Principal = Depends(current_user)):
    """One virtual-traffic run for the map's "Custom traffic" switch (ml/planning/microsim.py): the configured background traffic
    (density, vehicle mix, drivers, demand over time, road weights), and, when the plan has items, traffic that follows the plan
    (speed limits, closures, spawned groups, sources, zones, accidents ...). Returns the vehicle paths in the replay format plus
    statistics of the run. Fast (about a second); the baseline-vs-plan comparison with intervals is POST /plans/simulate."""
    scene = scene_of(body.scene)
    doc = body.plan.model_dump() if body.plan else {"props": [], "rules": [], "scenarios": []}
    tcm = mod("traffic_config")
    raw_cfg = body.traffic if body.traffic is not None else doc.get("traffic")
    problems = tcm.validate(raw_cfg)
    if problems:
        raise HTTPException(422, "the traffic settings have problems: " + "; ".join(problems[:3]))
    cfg = tcm.normalize(raw_cfg)
    n_items = sum(len(doc.get(k) or []) for k in ("props", "rules", "scenarios"))
    plans, ms = mod("plans"), mod("microsim")
    if n_items:
        val = await asyncio.to_thread(plans.validate, {**doc, "traffic": cfg}, scene)
        if not val["ok"]:
            raise HTTPException(422, "the plan has problems: " + "; ".join(m["message"] for m in (val["errors"] + val["conflicts"])[:3]))
    seed = body.seed if body.seed is not None else int(cfg["seed"])
    dur = body.duration_s if body.duration_s is not None else float(cfg["duration_s"])

    def work():
        r = ms.run(scene, doc if n_items else None, seed, duration_s=dur, keep_states=True, traffic=cfg)
        m = r["metrics"]
        return {"label": "virtual traffic (simplified model, not CARLA)", "follows_plan": bool(n_items), "plan_items": n_items, "seed": seed, "duration_s": dur,
                "vehicles": r["vehicles_total"], "spawned": r["spawned"], "config": cfg, "description": r["traffic"], "notes": ms.MODEL_NOTES,
                "stats": {k: m.get(k) for k in ("mean_speed_kmh", "p85_speed_kmh", "vehicles_in_region", "density_veh_per_km2", "throughput_veh_per_h",
                                                "travel_time_s", "queue_max_m", "stopped_share", "delay_s_per_vehicle")},
                "trajectories": ms.trajectories(r["states"])}

    return await asyncio.to_thread(work)
