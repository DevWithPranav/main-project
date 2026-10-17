"""Network and clock check between the two machines of the live test (standard library only: runs in the CarlaAir conda env too).

The live feed is frames + a capture timestamp from the simulator machine (Afif) to the detector machine (this one). Two things must
be right before any number from the test means anything:

  * bandwidth: 1920x1080 JPEG frames at about 20 fps are roughly 40 Mbit/s;
  * clocks: the pipeline measures latency as (its clock) - (the sender's capture time). If the two PCs' clocks differ, every latency
    is wrong by that difference. `ping` measures the offset (Cristian's algorithm on the lowest-RTT exchanges) and prints the value
    to pass to the pipeline as --clock-offset.

On the detector PC (this one), start the echo server (a different port from the pipeline's 5555):
    venv\\Scripts\\python.exe services/live/net_check.py listen

On the simulator PC (Afif), from any Python (3.8+):
    python net_check.py ping --to <detector tailscale ip>
"""

import argparse
import json
import socket
import statistics
import struct
import sys
import threading
import time

DEFAULT_PORT = 5556
NEEDED_MBIT = 40.0   # 1920x1080 JPEG q90, about 250 kB x 20 fps (measured sizes: tracker M4 rows)
CHUNK = 64 * 1024
_D = struct.Struct(">d")
_Q = struct.Struct(">Q")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            raise ConnectionError("connection closed")
        buf += part
    return bytes(buf)


def _serve_client(conn: socket.socket) -> None:
    with conn:
        try:
            while True:
                cmd = conn.recv(1)
                if not cmd or cmd == b"Q":
                    return
                if cmd == b"T":  # time exchange: reply with this machine's clock
                    _recv_exact(conn, 8)
                    conn.sendall(_D.pack(time.time()))
                elif cmd == b"B":  # upload test: swallow `size` bytes, then acknowledge
                    (size,) = _Q.unpack(_recv_exact(conn, 8))
                    left = size
                    while left > 0:
                        left -= len(conn.recv(min(CHUNK, left)) or (_ for _ in ()).throw(ConnectionError("closed")))
                    conn.sendall(b"K")
        except (ConnectionError, OSError):
            return


def serve(port: int = DEFAULT_PORT, bind: str = "0.0.0.0", stop: threading.Event | None = None, ready: threading.Event | None = None) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, port))
    srv.listen(4)
    srv.settimeout(0.5)
    if ready:
        ready.set()
    while not (stop and stop.is_set()):
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue
        print(f"[net_check] {addr[0]} connected", flush=True)
        threading.Thread(target=_serve_client, args=(conn,), daemon=True).start()
    srv.close()


def measure(host: str, port: int = DEFAULT_PORT, mb: float = 40.0, rounds: int = 24, timeout: float = 8.0) -> dict:
    """Round-trip time, clock offset (server minus this machine, seconds) and upload throughput to a `listen` server."""
    s = socket.create_connection((host, port), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    ex = []
    for _ in range(rounds):
        t0 = time.time()
        s.sendall(b"T" + _D.pack(t0))
        (srv_t,) = _D.unpack(_recv_exact(s, 8))
        t1 = time.time()
        ex.append((t1 - t0, srv_t - (t0 + t1) / 2.0))
    ex.sort()
    best = ex[: max(len(ex) // 4, 3)]  # the quickest exchanges carry the least asymmetry error
    offset = statistics.median(o for _, o in best)
    size = int(mb * 1024 * 1024)
    payload = b"\0" * CHUNK
    t0 = time.time()
    s.sendall(b"B" + _Q.pack(size))
    sent = 0
    while sent < size:
        n = min(CHUNK, size - sent)
        s.sendall(payload[:n])
        sent += n
    _recv_exact(s, 1)
    dt = time.time() - t0
    s.sendall(b"Q")
    s.close()
    rtts = [r for r, _ in ex]
    return {"host": host, "port": port, "rtt_ms": {"min": round(min(rtts) * 1000, 1), "median": round(statistics.median(rtts) * 1000, 1), "max": round(max(rtts) * 1000, 1)},
            "clock_offset_s": round(offset, 4), "clock_offset_ms": round(offset * 1000, 1),
            "upload_mbit_s": round(size * 8 / dt / 1e6, 1), "upload_seconds": round(dt, 2), "mb": mb}


def verdict(r: dict) -> list[str]:
    out = []
    mbit = r["upload_mbit_s"]
    if mbit >= 1.5 * NEEDED_MBIT:
        out.append(f"bandwidth OK: {mbit} Mbit/s (about {NEEDED_MBIT:g} needed for 1920x1080 at 20 fps)")
    elif mbit >= NEEDED_MBIT:
        out.append(f"bandwidth marginal: {mbit} Mbit/s; lower --jpeg-quality (e.g. 75) on the sender if frames are dropped")
    else:
        out.append(f"bandwidth TOO LOW: {mbit} Mbit/s < {NEEDED_MBIT:g}; use --jpeg-quality 60 and a smaller --width / --height on the sender, or a better link")
    if r["rtt_ms"]["median"] > 80:
        out.append(f"round trip {r['rtt_ms']['median']} ms is high (a relayed Tailscale path?): `tailscale ping <peer>` should say 'direct'")
    off = abs(r["clock_offset_ms"])
    if off > 50:
        out.append(f"CLOCKS DIFFER by {r['clock_offset_ms']} ms: latency numbers are wrong unless the pipeline gets --clock-offset {r['clock_offset_s']} (or sync both clocks: w32tm /resync)")
    else:
        out.append(f"clocks agree within {off} ms: no --clock-offset needed")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("listen", help="echo server on the detector PC")
    a.add_argument("--port", type=int, default=DEFAULT_PORT)
    a.add_argument("--bind", default="0.0.0.0")
    b = sub.add_parser("ping", help="measure from the simulator PC")
    b.add_argument("--to", required=True, help="detector PC address (its Tailscale IP)")
    b.add_argument("--port", type=int, default=DEFAULT_PORT)
    b.add_argument("--mb", type=float, default=40.0, help="size of the upload test")
    b.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.cmd == "listen":
        print(f"[net_check] listening on {args.bind}:{args.port} (Ctrl+C to stop)", flush=True)
        try:
            serve(args.port, args.bind)
        except KeyboardInterrupt:
            pass
        return
    try:
        r = measure(args.to, args.port, args.mb)
    except (OSError, ConnectionError) as e:
        sys.exit(f"cannot reach {args.to}:{args.port} ({e}). Is `net_check.py listen` running there, Tailscale connected on both, and the firewall rule open (scripts/live_test/02_open_firewall.ps1)?")
    if args.json:
        print(json.dumps(r, indent=1))
        return
    print(f"to {r['host']}:{r['port']}")
    print(f"  round trip   min {r['rtt_ms']['min']} / median {r['rtt_ms']['median']} / max {r['rtt_ms']['max']} ms")
    print(f"  clock offset {r['clock_offset_ms']} ms (that PC's clock minus this one)")
    print(f"  upload       {r['upload_mbit_s']} Mbit/s ({r['mb']:g} MB in {r['upload_seconds']} s)")
    for line in verdict(r):
        print("  ->", line)
    print(f"\nclock offset to use on the detector PC: --clock-offset {r['clock_offset_s']}")


if __name__ == "__main__":
    main()
