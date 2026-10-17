r"""Traceability check (Expected_Output 9 last item, PRD Objective 6): every event from raw footage to the
dashboard and on to a report or a recommendation an authority can act on.

For every event of every session in the database (through the running API):

    footage         the frame it was flagged on exists: CARLA recordings/<flight>/frames/<frame>.jpg, or a
                    frame index inside the real clip's video (its site file names the video)
    dashboard       GET /api/events/{id} returns it
    evidence        counted events (flagged / needs_review) have a clip the API serves (Range request)
    report          its id is in the session's CSV export (the PDF / XLSX / GeoJSON come from the same query)
    recommendation  it is in a recommendation's evidence.event_ids (only events in a cluster / pattern)

Traceable = footage + dashboard + (report or recommendation), and evidence for counted events.

Usage (backend on :8000, sessions imported):
    venv\Scripts\python.exe backend/trace_check.py
    venv\Scripts\python.exe backend/trace_check.py --out ml/data/results/trace_check.json
"""

import argparse
import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.app import config  # noqa: E402

COUNTED = {"flagged", "needs_review"}


class Api:
    def __init__(self, base: str):
        self.base, self.tok = base.rstrip("/"), None

    def call(self, path: str, raw: bool = False, headers: dict | None = None):
        h = {"Authorization": f"Bearer {self.tok}"} if self.tok else {}
        h.update(headers or {})
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=h), timeout=120) as r:
                body = r.read()
                return (r.status, body) if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            return (e.code, b"") if raw else None

    def login(self, user: str) -> None:
        req = urllib.request.Request(self.base + "/api/auth/login", data=json.dumps({"username": user, "password": f"{user}123"}).encode(),
                                     headers={"Content-Type": "application/json"})
        self.tok = json.loads(urllib.request.urlopen(req).read())["access_token"]


def frame_checker(session: dict):
    """frame -> bool: does the raw footage have this frame?"""
    flight, meta = session.get("flight") or session["session_id"], session.get("import") or {}
    if session.get("source") == "video":
        vdir = REPO / (meta.get("violations_dir") or "")
        summ = json.loads((vdir / "summary.json").read_text(encoding="utf-8")) if (vdir / "summary.json").is_file() else {}
        site = Path(summ.get("site", "").replace("\\", "/"))
        site = site if site.is_absolute() else REPO / site
        video = json.loads(site.read_text(encoding="utf-8")).get("video", "") if site.is_file() else ""
        vp = Path(video) if Path(video).is_absolute() else REPO / video
        if not vp.is_file():
            return lambda f: False, f"video not found ({video})"
        import cv2
        n = int(cv2.VideoCapture(str(vp)).get(cv2.CAP_PROP_FRAME_COUNT))
        return (lambda f: 0 <= int(f) < n), f"{vp.name} ({n} frames)"
    frames = config.RECORDINGS_DIR / flight / "frames"
    if not frames.is_dir():
        return lambda f: False, f"no frames folder {frames}"
    return (lambda f: (frames / f"{int(f):05d}.jpg").is_file()), f"{frames.relative_to(REPO)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    api = Api(a.base)
    api.login("planner")

    totals, rows, bad = Counter(), [], []
    for s in api.call("/api/sessions"):
        sid = s["session_id"]
        if s.get("source") == "live" or sid.startswith(("live_", "e2e_")):
            continue
        q = urllib.parse.quote(sid, safe="")
        s = api.call(f"/api/sessions/{q}") or s
        has_frame, src = frame_checker(s)
        events = []
        for off in range(0, 100000, 500):
            page = api.call(f"/api/events?session_id={q}&limit=500&offset={off}")
            events += page["items"]
            if len(events) >= page["total"] or not page["items"]:
                break
        st, body = api.call(f"/api/export?format=csv&session_id={q}", raw=True)
        in_report = {r.get("event_id") for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig")))} if st == 200 else set()
        recs = api.call(f"/api/recommendations?session_id={q}") or []
        in_rec = defaultdict(list)
        for r in recs:
            for eid in (r.get("evidence") or {}).get("event_ids", []):
                in_rec[eid].append(r["id"])
        c = Counter()
        for e in events:
            eid = e["event_id"]
            frame = e.get("flag_frame", e.get("frame"))
            ok = {"footage": frame is not None and has_frame(frame),
                  "dashboard": api.call(f"/api/events/{urllib.parse.quote(eid, safe='')}") is not None,
                  "report": eid in in_report, "recommendation": eid in in_rec}
            counted = e.get("status") in COUNTED
            url = (e.get("evidence") or {}).get("clip_url")
            ok["evidence"] = (api.call(url, raw=True, headers={"Range": "bytes=0-1023"})[0] == 206) if url else False
            traceable = ok["footage"] and ok["dashboard"] and (ok["report"] or ok["recommendation"]) and (ok["evidence"] or not counted)
            for k, v in ok.items():
                c[k] += v and (k != "evidence" or counted)  # evidence: counted events only (suppressed ones have no clip)
            c["counted"] += counted
            c["traceable"] += traceable
            c["events"] += 1
            if not traceable:
                bad.append({"session": sid, "event_id": eid, "status": e.get("status"), **ok})
        totals.update(c)
        rows.append({"session": sid, "source": s.get("source"), "footage": src, **c})
        print(f"{sid[:45]:45s} {c['events']:4d} events: traceable {c['traceable']}/{c['events']}  footage {c['footage']}  dashboard "
              f"{c['dashboard']}  evidence {c['evidence']}/{c['counted']} counted  report {c['report']}  in a recommendation {c['recommendation']}")
    print(f"\nALL: {totals['traceable']}/{totals['events']} events traceable (footage {totals['footage']}, dashboard {totals['dashboard']}, "
          f"evidence {totals['evidence']}/{totals['counted']} counted, report {totals['report']}, recommendation {totals['recommendation']})")
    for b in bad[:20]:
        print("  not traceable:", b)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps({"totals": totals, "sessions": rows, "not_traceable": bad}, indent=1, default=int))
    return 0 if totals["traceable"] == totals["events"] else 1


if __name__ == "__main__":
    sys.exit(main())
