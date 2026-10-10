"""Stand-in for the backend's two live routes (backend/API.md), for testing the pipeline before the
real backend runs. Standard library only. Logs every POST /api/events and a count of
POST /api/live/state to a JSON-lines file with the receive time, checks the service token.

Usage:
    venv\\Scripts\\python.exe services/live/mock_backend.py --port 8000 --log ml/data/results/live/mock_backend.jsonl
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_handler(log_path: Path, token: str):
    lock = threading.Lock()
    counts = {"events": 0, "states": 0, "vehicles": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/api/health"):
                self._reply(200, {"db": "mock", "redis": "mock", "s3": "mock", **counts})
            else:
                self._reply(404, {"detail": "mock backend: only /api/health, POST /api/events, /api/live/state"})

        def do_POST(self):
            now = time.time()
            if self.headers.get("Authorization") != f"Bearer {token}":
                return self._reply(401, {"detail": "bad service token"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
            with lock:
                if self.path == "/api/events":
                    counts["events"] += 1
                    with open(log_path, "a") as f:
                        f.write(json.dumps({"recv_wall": now, "route": "events", "event": body}) + "\n")
                    return self._reply(201, body)
                if self.path == "/api/live/state":
                    counts["states"] += 1
                    counts["vehicles"] += len(body or [])
                    return self._reply(200, {"n": len(body or [])})
            self._reply(404, {"detail": "not found"})

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log", type=Path, default=Path("ml/data/results/live/mock_backend.jsonl"))
    args = ap.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.log, os.environ.get("SERVICE_TOKEN", "dev-service-token")))
    print(f"[mock backend] http://localhost:{args.port}/api  log -> {args.log}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
