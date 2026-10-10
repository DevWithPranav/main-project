r"""UI check of the dashboard and the twin in headless Chrome (Build Plan M10 prep, 2026-10-10).

Logs in (dashboard: the token in localStorage, as its login page stores it; twin: its own login
form), opens every dashboard page and the twin on one session, and per page records JavaScript
errors, failed /api requests and the amount of rendered text, plus a screenshot. Chrome DevTools
Protocol over websockets: no Playwright needed.

Usage (backend :8000, `npm run dev` in frontend/ and twin/, a session imported):
    venv\Scripts\python.exe backend/ui_check.py --session 20261009_201727 --town Town05
Screenshots: <out>/<page>.png (default ml/data/results/ui_check/<time>/). Exit code 1 if a page
has a JavaScript error or a failed /api request.
"""

import argparse
import base64
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

REPO = Path(__file__).resolve().parents[1]
CHROMES = [Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
           Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")]
PAGES = ["live", "violations", "stats", "sessions", "config", "recommendations"]


class Page:
    def __init__(self, ws_url: str):
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=20)
        self.n = 0
        self.errors: list[str] = []
        self.failed: list[str] = []
        self.requests: dict[str, str] = {}
        for d in ("Page", "Runtime", "Log", "Network"):
            self.cmd(f"{d}.enable")

    def _event(self, m: dict) -> None:
        meth, p = m.get("method"), m.get("params", {})
        if meth == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            self.errors.append((d.get("exception", {}).get("description") or d.get("text", ""))[:200])
        elif meth == "Runtime.consoleAPICalled" and p.get("type") == "error":
            self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:200])
        elif meth == "Network.requestWillBeSent":
            self.requests[p["requestId"]] = p["request"]["url"]
        elif meth == "Network.responseReceived":
            url, st = p["response"]["url"], p["response"]["status"]
            if "/api/" in url and st >= 400:
                self.failed.append(f"{st} {url.split('/api/', 1)[1][:80]}")
        elif meth == "Network.loadingFailed":
            url = self.requests.get(p["requestId"], "")
            if "/api/" in url and not p.get("canceled"):
                self.failed.append(f"{p.get('errorText')} {url.split('/api/', 1)[1][:80]}")

    def cmd(self, method: str, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv(timeout=60))
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            self._event(m)

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            try:
                self._event(json.loads(self.ws.recv(timeout=max(0.05, end - time.time()))))
            except TimeoutError:
                break

    def js(self, expr: str):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def goto(self, url: str, wait: float) -> None:
        self.cmd("Page.navigate", url=url)
        self.pump(wait)

    def shot(self, path: Path) -> None:
        path.write_bytes(base64.b64decode(self.cmd("Page.captureScreenshot", format="png")["data"]))

    def reset(self) -> tuple[list[str], list[str]]:
        e, f = self.errors, self.failed
        self.errors, self.failed = [], []
        return e, f


def login_token(base: str, user: str) -> str:
    req = urllib.request.Request(f"{base}/api/auth/login", data=json.dumps({"username": user, "password": f"{user}123"}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True)
    ap.add_argument("--town", default=None)
    ap.add_argument("--user", default="admin", help="Dashboard user (admin sees every page)")
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--dashboard", default="http://localhost:5173")
    ap.add_argument("--twin", default="http://localhost:5174")
    ap.add_argument("--wait", type=float, default=6.0, help="Seconds per page")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    out = a.out or REPO / "ml" / "data" / "results" / "ui_check" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    chrome = next((c for c in CHROMES if c.exists()), None)
    if chrome is None:
        sys.exit("no Chrome / Edge found")
    prof = Path(tempfile.mkdtemp(prefix="ui_check_"))
    port = 9333
    proc = subprocess.Popen([str(chrome), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                             "--window-size=1600,1000", "--no-first-run", "--disable-gpu-sandbox", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows, bad = [], 0
    try:
        for _ in range(50):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2).read())
                if any(t.get("type") == "page" for t in tabs):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
        tab = next(t for t in tabs if t.get("type") == "page")
        pg = Page(tab["webSocketDebuggerUrl"])

        token = login_token(a.backend, a.user)
        pg.goto(f"{a.dashboard}/login", 3)
        pg.js(f"localStorage.setItem('aerial.token', {json.dumps(token)})")
        pg.reset()
        for name in PAGES:
            pg.goto(f"{a.dashboard}/{name}?session={a.session}", a.wait)
            text = pg.js("document.body.innerText.length") or 0
            path = pg.js("location.pathname")
            pg.shot(out / f"dashboard_{name}.png")
            errs, failed = pg.reset()
            ok = not errs and not failed and path == f"/{name}"
            bad += not ok
            rows.append((f"dashboard /{name}", ok, f"at {path}, {text} chars, {len(errs)} JS errors, {len(failed)} failed api"
                         + (f": {(errs + failed)[:2]}" if errs or failed else "")))

        q = f"?session={a.session}" + (f"&town={a.town}" if a.town else "")
        pg.goto(f"{a.twin}/{q}", 4)
        pg.js(f"document.getElementById('username').value = 'planner'; document.getElementById('password').value = 'planner123';"
              " document.getElementById('loginBtn').click(); true")
        pg.pump(a.wait * 2.5)
        info = pg.js("({login: document.getElementById('login').hidden, canvas: document.querySelectorAll('canvas').length,"
                     " err: (document.getElementById('loginErr') || {}).textContent || '',"
                     " session: (document.getElementById('session') || {}).value || ''})") or {}
        pg.shot(out / "twin.png")
        errs, failed = pg.reset()
        ok = bool(info.get("login")) and info.get("canvas", 0) > 0 and not errs and not failed
        bad += not ok
        rows.append(("twin", ok, f"logged in {info.get('login')}, session {info.get('session')!r}, {info.get('canvas')} canvas, "
                     f"{len(errs)} JS errors, {len(failed)} failed api" + (f": {(errs + failed)[:2]}" if errs or failed else "")
                     + (f", login error {info.get('err')!r}" if info.get("err") else "")))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)
    for name, ok, detail in rows:
        print(f"[{'ok' if ok else 'FAIL':4s}] {name:26s} {detail}")
    print(f"\nscreenshots -> {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
