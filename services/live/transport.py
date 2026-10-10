"""Frame transport for the live pipeline (Build Plan M4, decision #4): simulator or replay -> detector.

One TCP connection, length-prefixed messages, standard library only (it must import in both
venv_sim, Python 3.10 with no zmq, and the main venv). Each message is

    MAGIC (4 bytes, b"AVF1") | header length (uint32 BE) | payload length (uint32 BE) | header JSON | payload

The first message of a connection is a "hello" (payload empty) with the camera and the session:
    {"type": "hello", "width", "height", "fov", "ground_z", "map", "source", "flight"?}
then one "frame" message per camera image (payload = JPEG bytes):
    {"type": "frame", "frame", "carla_frame", "sim_time", "pose": [x, y, z, pitch, yaw, roll],
     "capture_wall": unix time when the frame was captured (latency is measured from it)}
and a final {"type": "end"}.

Latest-frame-wins on both sides, so a slow detector never builds up latency (the pattern
imagezmq recommends for PUB/SUB; docs/research/M4_live.md): the sender keeps a queue of at most
`queue` frames and drops the oldest; the receiver's IO thread reads every message off the socket
at line rate and keeps only the newest frame (mode "latest"), or all of them (mode "all", for
runs that must see every frame, e.g. comparing with the offline pipeline).

The detector side listens (FrameReceiver, default 0.0.0.0:5555); sources connect (FrameSender)
and reconnect if the connection drops. Same code on one machine or two.

Usage: imported by simulation/carla_scripts/stream_flight.py, services/live/replay_source.py and
services/live/pipeline.py.
"""

import json
import socket
import struct
import threading
import time
from collections import deque

MAGIC = b"AVF1"
_HDR = struct.Struct(">4sII")
MAX_HEADER = 1 << 20
MAX_PAYLOAD = 64 << 20
DEFAULT_PORT = 5555


def pack(header: dict, payload: bytes = b"") -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return _HDR.pack(MAGIC, len(h), len(payload)) + h + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def read_message(sock: socket.socket) -> tuple[dict, bytes] | None:
    """One message off a socket, or None when the peer closed the connection."""
    head = _recv_exact(sock, _HDR.size)
    if head is None:
        return None
    magic, hl, pl = _HDR.unpack(head)
    if magic != MAGIC or hl > MAX_HEADER or pl > MAX_PAYLOAD:
        raise ValueError(f"bad frame header {head!r}")
    h = _recv_exact(sock, hl)
    p = _recv_exact(sock, pl) if pl else b""
    if h is None or p is None:
        return None
    return json.loads(h), p


class Decoder:
    """Incremental decoder for a byte stream (the receiver uses read_message; this is for tests
    and for any transport that hands over arbitrary chunks)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[dict, bytes]]:
        self.buf += data
        out = []
        while len(self.buf) >= _HDR.size:
            magic, hl, pl = _HDR.unpack_from(self.buf)
            if magic != MAGIC:
                raise ValueError("bad magic")
            n = _HDR.size + hl + pl
            if len(self.buf) < n:
                break
            h = json.loads(bytes(self.buf[_HDR.size:_HDR.size + hl]))
            out.append((h, bytes(self.buf[_HDR.size + hl:n])))
            del self.buf[:n]
        return out


class FrameSender:
    """Client side (simulator / replay). send() never blocks the caller: frames go into a queue of
    at most `queue` items (oldest dropped) that a thread writes to the socket. The hello is re-sent
    after every reconnect."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, hello: dict | None = None, queue: int = 2,
                 reconnect_s: float = 1.0):
        self.addr = (host, port)
        self.hello = hello or {"type": "hello"}
        self.q: deque = deque(maxlen=queue)
        self.cv = threading.Condition()
        self.reconnect_s = reconnect_s
        self.sent = self.dropped = 0
        self.connected = False
        self._stop = False
        self._end = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def send(self, header: dict, payload: bytes = b"") -> None:
        with self.cv:
            if len(self.q) == self.q.maxlen:
                self.dropped += 1
            self.q.append((header, payload))
            self.cv.notify()

    def close(self, timeout: float = 5.0) -> None:
        """Send what is queued, then an "end" message, and stop."""
        with self.cv:
            self._end = True
            self.cv.notify()
        self._thread.join(timeout)
        self._stop = True

    def _connect(self) -> socket.socket | None:
        try:
            s = socket.create_connection(self.addr, timeout=3.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(None)
            s.sendall(pack(self.hello))
            self.connected = True
            return s
        except OSError:
            self.connected = False
            return None

    def _run(self) -> None:
        sock = None
        while not self._stop:
            with self.cv:
                while not self.q and not self._end:
                    self.cv.wait(0.5)
                item = self.q.popleft() if self.q else None
                ending = self._end and not self.q and item is None
            while sock is None and not self._stop:
                sock = self._connect()
                if sock is None:
                    if ending:
                        return
                    time.sleep(self.reconnect_s)
            if sock is None:
                return
            try:
                if item is not None:
                    sock.sendall(pack(*item))
                    self.sent += 1
                if ending:
                    sock.sendall(pack({"type": "end"}))
                    sock.close()
                    return
            except OSError:
                sock = None
                self.connected = False


class FrameReceiver:
    """Server side (detector). Accepts one source at a time; get() returns the next frame to process.

    mode "latest": only the newest unprocessed frame is kept (older ones count as dropped).
    mode "all": every frame is queued (bounded by max_queue; then the oldest is dropped)."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT, mode: str = "latest", max_queue: int = 10000):
        if mode not in ("latest", "all"):
            raise ValueError(mode)
        self.mode = mode
        self.q: deque = deque(maxlen=1 if mode == "latest" else max_queue)
        self.cv = threading.Condition()
        self.hello: dict | None = None
        self.received = self.dropped = 0
        self.ended = False
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                while True:
                    msg = read_message(conn)
                    if msg is None:
                        break
                    h, payload = msg
                    h["recv_wall"] = time.time()
                    with self.cv:
                        if h.get("type") == "hello":
                            self.hello = h
                        elif h.get("type") == "end":
                            self.ended = True
                        elif h.get("type") == "frame":
                            self.received += 1
                            if len(self.q) == self.q.maxlen:
                                self.dropped += 1
                            self.q.append((h, payload))
                        self.cv.notify_all()
                    if h.get("type") == "end":
                        break
            except (OSError, ValueError) as e:
                print(f"[transport] connection error: {e}")
            finally:
                conn.close()

    def wait_hello(self, timeout: float | None = None) -> dict | None:
        with self.cv:
            self.cv.wait_for(lambda: self.hello is not None, timeout)
            return self.hello

    def get(self, timeout: float = 1.0) -> tuple[dict, bytes] | None:
        """Next frame, or None if nothing arrived within timeout (check .ended for end of stream)."""
        with self.cv:
            if not self.q:
                self.cv.wait_for(lambda: bool(self.q) or self.ended, timeout)
            return self.q.popleft() if self.q else None

    def close(self) -> None:
        self._srv.close()
