r"""End-to-end check of the whole system without the simulator (Build Plan M10 prep, 2026-10-10).

Walks the path a session takes through the platform, against the running services, and prints one
line per step with what it measured:

    health -> login (each role) -> import processed flights -> events + evidence clip -> trajectories
    -> statistics -> exports (csv / geojson / xlsx / pdf, timed: PRD <= 10 s) -> recommendations (M8)
    -> a planner decision -> planner history -> profiles / conditions / scenes (+ town objects for the
    twin) -> live: a vehicle state and an event posted with the service token arrive
    on the WebSocket (timed) -> dashboard (:5173) and twin (:5174) dev servers serve their page and
    proxy /api to the backend.

Writes go to the dev database (sessions are re-imported in place; the live check posts to session
"e2e_check"). Exit code 1 if any step fails.

Usage (docker compose up -d; backend on :8000; `npm run dev` in frontend/ and twin/):
    venv\Scripts\python.exe backend/e2e_check.py 20261009_201727 20261010_120643:violations_s2
    venv\Scripts\python.exe backend/e2e_check.py --skip-import --no-ui
A flight is <flight>[:<violations dir>] (default: the newest violations* folder); a real clip is
video:<clip folder under video_validation>[:<run>].
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICE_TOKEN = "dev-service-token"
ROLES = ("officer", "operator", "planner", "maintenance", "admin")
# Expected_Output 7.3: every recommendation carries all of these
REC_FIELDS = ("problem", "location", "evidence", "action", "expected_impact", "priority", "validation_method",
              "confidence", "limitations", "alternatives")

results: list[tuple[bool, str, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f"[{'ok' if ok else 'FAIL':4s}] {name:34s} {detail}", flush=True)
    return ok


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.tokens: dict[str, str] = {}

    def call(self, method: str, path: str, user: str | None = None, body=None, raw: bool = False,
             headers: dict | None = None, timeout: float = 600):
        h = dict(headers or {})
        if user == "service":
            h["Authorization"] = f"Bearer {SERVICE_TOKEN}"
        elif user:
            h["Authorization"] = f"Bearer {self.tokens[user]}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = r.read()
            dt = time.perf_counter() - t0
            if raw:
                return r.status, out, dt, {k.lower(): v for k, v in r.headers.items()}
            return json.loads(out) if out else None, dt

    def login(self, user: str) -> None:
        out, _ = self.call("POST", "/api/auth/login", body={"username": user, "password": f"{user}123"})
        self.tokens[user] = out["access_token"]


def check_import(api: Api, flights: list[str]) -> list[str]:
    sids = []
    for spec in flights:
        if spec.startswith("video:"):  # a processed real clip: video:<clip folder>[:<run>]
            clip, _, run = spec[6:].partition(":")
            body = {"flight": clip, "source": "video", **({"run": run} if run else {})}
        else:
            flight, _, vdir = spec.partition(":")
            body = {"flight": flight, **({"violations_dir": vdir} if vdir else {})}
        try:
            s, dt = api.call("POST", "/api/sessions/import", "operator", body)
            imp = s.get("import", {})
            # a clip with no violations is a valid session (e.g. the UIT roundabouts): needs tracks, no rejects
            step(f"import {spec}", imp.get("n_rejected", 1) == 0 and (imp.get("n_imported", 0) > 0 or imp.get("n_tracks", 0) > 0),
                 f"{imp.get('n_imported')} events ({imp.get('n_rejected')} rejected), {imp.get('clips')} clips, "
                 f"{imp.get('n_tracks')} tracks, {imp.get('n_anomalies', 0)} anomalies, {dt:.1f} s")
            sids.append(s["session_id"])
        except urllib.error.HTTPError as e:
            step(f"import {spec}", False, f"HTTP {e.code}: {e.read()[:200]!r}")
    return sids


def check_session(api: Api, sid_raw: str) -> None:
    sid = urllib.parse.quote(sid_raw, safe="")  # real clips' folder names have spaces and commas
    ev, _ = api.call("GET", f"/api/events?session_id={sid}&limit=500", "officer")
    items = ev["items"]
    types = sorted({e["type"] for e in items})
    step(f"events {sid_raw[:40]}", all(e.get("condition") or e.get("kind") == "anomaly" for e in items),
         f"{ev['total']} events, types {types}")
    clip = next((e["evidence"]["clip_url"] for e in items if (e.get("evidence") or {}).get("clip_url")), None)
    if clip:
        st, body, dt, hd = api.call("GET", clip, raw=True, headers={"Range": "bytes=0-1023"})
        step(f"evidence clip {sid_raw[:40]}", st == 206 and len(body) == 1024, f"{hd.get('content-type')}, {dt * 1000:.0f} ms")
    else:
        step(f"evidence clip {sid_raw[:40]}", True, "no clips in this violations folder (render_violations.py --clips)")
    tr, dt = api.call("GET", f"/api/sessions/{sid}/trajectories", "officer")
    pts = sum(len(v) for v in tr.values())
    step(f"trajectories {sid_raw[:40]}", len(tr) > 0 and pts > 0, f"{len(tr)} tracks, {pts} points, {dt:.2f} s")
    st, _ = api.call("GET", f"/api/stats?session_id={sid}", "officer")
    step(f"stats {sid_raw[:40]}", sum(st["by_type"].values()) == ev["total"] if isinstance(st.get("by_type"), dict) else bool(st),
         f"by_type {st.get('by_type')}, {len(st.get('hotspots') or [])} hotspots")
    recs, dt = api.call("GET", f"/api/recommendations?session_id={sid}", "planner")
    missing = sorted({f for r in recs for f in REC_FIELDS if f not in r})
    step(f"recommendations {sid_raw[:40]}", isinstance(recs, list) and not missing,
         f"{len(recs)} recommendations, {dt:.1f} s" + (f", missing fields {missing}" if missing else ""))
    if recs:
        r = recs[0]
        api.call("POST", f"/api/recommendations/{urllib.parse.quote(r['id'])}/decision", "planner",
                 {"decision": "modified", "rationale": "e2e_check: decision path"})
        hist, _ = api.call("GET", "/api/planner/history", "planner")
        items_h = hist if isinstance(hist, list) else hist.get("items", [])
        step(f"planner decision {sid_raw[:40]}", any(h.get("rec_id", h.get("recommendation_id")) == r["id"] or h.get("id") == r["id"]
                                            or json.dumps(h, default=str).find(r["id"]) >= 0 for h in items_h),
             f"recorded; history has {len(items_h)} entries")


def check_exports(api: Api, sid: str | None) -> None:
    q = f"&session_id={urllib.parse.quote(sid, safe='')}" if sid else ""
    for fmt in ("csv", "geojson", "xlsx", "pdf"):
        st, body, dt, hd = api.call("GET", f"/api/export?format={fmt}{q}", "officer", raw=True)
        step(f"export {fmt}", st == 200 and len(body) > 0 and dt <= 10.0,
             f"{len(body) / 1024:.0f} kB, {hd.get('x-event-count', '?')} events, {dt:.2f} s (PRD <= 10 s)")


def check_config(api: Api) -> None:
    prof, _ = api.call("GET", "/api/profiles", "officer")
    step("profiles", len(prof) > 0, f"{len(prof)} profiles")
    cond, _ = api.call("GET", "/api/conditions", "officer")
    n = len(cond) if isinstance(cond, list) else len(cond.get("conditions", []))
    step("conditions", n >= 34, f"{n} conditions")
    scenes, _ = api.call("GET", "/api/scenes", "officer")
    names = [s if isinstance(s, str) else s.get("town", s.get("name")) for s in scenes]
    step("scenes", len(names) > 0, f"{names}")
    for town in ("Town03", "Town05"):
        if town in names:
            sc, dt = api.call("GET", f"/api/scenes/{town}", "officer")
            try:
                ob, dt2 = api.call("GET", f"/api/scenes/{town}/objects", "officer")
                n_obj = len(ob.get("objects", ob)) if isinstance(ob, (dict, list)) else 0
            except urllib.error.HTTPError as e:
                n_obj, dt2 = f"HTTP {e.code}", 0
            step(f"twin data {town}", len(sc.get("lanes", [])) > 0,
                 f"{len(sc.get('lanes', []))} lanes ({dt:.2f} s), objects {n_obj} ({dt2:.2f} s)")


def check_anomalies(api: Api) -> None:
    try:
        a, _ = api.call("GET", "/api/anomalies", "maintenance")
        items = a.get("items", a) if isinstance(a, dict) else a
        st, _ = api.call("GET", "/api/anomalies/stats", "maintenance")
        step("anomalies (M9)", True, f"{len(items)} anomalies; stats {json.dumps(st)[:120]}")
    except urllib.error.HTTPError as e:
        step("anomalies (M9)", False, f"HTTP {e.code}")


def check_live(api: Api, ws_base: str) -> None:
    from websockets.sync.client import connect
    sid = "e2e_check"
    url = f"{ws_base}/api/ws/live?session_id={sid}&token={api.tokens['officer']}"
    with connect(url, open_timeout=10) as ws:
        time.sleep(0.3)
        t0 = time.time()
        api.call("POST", "/api/live/state", "service", [{"session_id": sid, "track_id": 1, "cls": "car", "x": 0.0, "y": 0.0,
                                                         "speed_kmh": 30.0, "heading_deg": 0.0, "t_s": 0.0}])
        ev_id = f"e2e_check-{int(t0)}"
        api.call("POST", "/api/events", "service", {
            "event_id": ev_id, "kind": "violation", "type": "speeding", "session_id": sid, "track_ids": [1],
            "cls": "car", "start_s": 0.0, "start_frame": 0, "flag_s": 0.0, "flag_frame": 0, "x": 0.0, "y": 0.0,
            "status": "flagged", "confidence": 0.9, "tags": ["e2e_check"], "value": {}})
        got, lat = set(), {}
        while time.time() - t0 < 5 and got != {"vehicles", "event"}:
            try:
                m = json.loads(ws.recv(timeout=5))
            except TimeoutError:
                break
            if m.get("type") in ("vehicles", "event") and m["type"] not in got:
                got.add(m["type"])
                lat[m["type"]] = time.time() - t0
        step("live WebSocket", got == {"vehicles", "event"},
             ", ".join(f"{k} after {v * 1000:.0f} ms" for k, v in lat.items()) or "nothing received")


def check_ui(port: int, name: str) -> None:
    # "localhost": Vite listens on ::1 only on Windows
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/", timeout=10) as r:
            page = r.read().decode(errors="replace")
        with urllib.request.urlopen(f"http://localhost:{port}/api/health", timeout=10) as r:
            h = json.loads(r.read())
        step(f"{name} :{port}", "<div id=" in page and h.get("db") == "ok", "page served, /api proxied to the backend")
    except Exception as e:  # noqa: BLE001
        step(f"{name} :{port}", False, f"{e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("flights", nargs="*", help="<flight>[:<violations dir>] to import")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--skip-import", action="store_true", help="Check the sessions already in the database")
    ap.add_argument("--no-ui", action="store_true", help="Skip the dashboard / twin dev-server checks")
    a = ap.parse_args()
    api = Api(a.base)

    h, _ = api.call("GET", "/api/health")
    if not step("health", all(h.get(k) == "ok" for k in ("db", "redis", "s3")), json.dumps(h)):
        return 1
    for u in ROLES:
        api.login(u)
    me, _ = api.call("GET", "/api/me", "planner")
    step("login (5 roles)", me.get("role") == "PLANNER", f"{len(api.tokens)} tokens")

    sids = [] if a.skip_import else check_import(api, a.flights)
    if not sids:
        sessions, _ = api.call("GET", "/api/sessions", "officer")
        sids = [s["session_id"] for s in sessions if not s["session_id"].startswith(("live_", "e2e_"))][:3]
    for sid in sids:
        check_session(api, sid)
    check_exports(api, sids[0] if sids else None)
    check_config(api)
    # road-surface anomalies (M9) dropped from the project 2026-10-10: check_anomalies() not run
    check_live(api, a.base.replace("http", "ws", 1))
    if not a.no_ui:
        check_ui(5173, "dashboard")
        check_ui(5174, "twin")

    n_fail = sum(not ok for ok, _, _ in results)
    print(f"\n{len(results) - n_fail}/{len(results)} steps ok" + (f", {n_fail} failed" if n_fail else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
