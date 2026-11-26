"""Worker web server (contract messages 7 and 8): FastAPI + uvicorn in a thread on 127.0.0.1:8001 only.

    GET  /stream/{camera_id}  MJPEG (multipart/x-mixed-replace) of the latest annotated frame, ~2 FPS, 1344 px, q70.
                              The newest frame goes out at once on connect, then ~2 FPS. ?raw=1: the same stream
                              without any drawings (same size). 404 for an unknown camera or one disabled in the DB.
    POST /reload              X-RADS-Internal token; answers {"ok": true} at once and only sets a flag
    GET  /health              per camera status / fps / last frame, open incidents, crossing check (no token)

The main loop hands frames over through FrameHub.publish(): references only, no copy, no drawing, no encoding.
Drawing and JPEG encoding happen here, in a small fixed pool of encoder threads, once per new frame and shared by
all viewers of the same kind (annotated or raw), and only while that kind has at least one viewer – with no viewers
the cost is ~0. A client that leaves after one frame (a snapshot) is normal: Starlette cancels its stream, the viewer
is counted out in a `finally`, and only a DEBUG line is logged.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import cv2
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from shared.contract import (INTERNAL_HEADER, PATH_WORKER_RELOAD, PATH_WORKER_STREAM, STREAM_FPS, WORKER_HOST,
                             WORKER_PORT, ReloadResponse, now_iso)
from worker.draw import annotate, draw_banner, resize_for_display

log = logging.getLogger(__name__)

STREAM_WIDTH = 1344
STREAM_JPEG_QUALITY = 70
BOUNDARY = "frame"
POLL_S = 0.05
LOCALHOST = "127.0.0.1"
ENCODE_THREADS = 2


def kind(raw: bool) -> str:
    return "raw" if raw else "annotated"


@dataclass
class _Slot:
    seq: int = 0              # bumped by every publish
    image: Any = None
    result: Any = None
    zones: Any = ()
    lines: Any = ()
    banner: str | None = None  # detection paused: the annotated stream shows only this banner (raw: nothing)
    jpegs: dict[bool, tuple[int, bytes]] = field(default_factory=dict)   # raw? -> (seq it was made from, JPEG)


class FrameHub:
    """Hand-off point between the main loop (publish, set_health) and the web server (stream, health)."""

    def __init__(self, camera_ids=(), disabled=()) -> None:
        self._lock = threading.Lock()
        self._slots: dict[int, _Slot] = {c: _Slot() for c in camera_ids}
        self._disabled: set[int] = set(disabled)                 # disabled in the DB: /stream answers 404
        self._viewers: dict[tuple[int, bool], int] = {}           # (camera, raw?) -> open streams
        self._health: dict[str, Any] = {}
        self._enc_locks: dict[tuple[int, bool], asyncio.Lock] = {}
        self._pool: ThreadPoolExecutor | None = None              # encoder threads, started on first use
        self.encodes = {"annotated": 0, "raw": 0}
        self.closed = threading.Event()

    # ---------------------------------------------------------------- main loop side (never waits on I/O)

    def publish(self, camera_id: int, image, result, zones, lines: list[str], banner: str | None = None) -> None:
        """Store references to the newest frame and its results. Callers must not modify them afterwards.
        With `banner` (detection paused) `result` may be None: the stream shows the plain frame + the banner."""
        with self._lock:
            s = self._slots.get(camera_id)
            if s is not None:
                s.image, s.result, s.zones, s.lines, s.banner = image, result, zones, lines, banner
                s.seq += 1

    def set_health(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._health = snapshot

    def set_cameras(self, camera_ids) -> None:
        with self._lock:
            ids = set(camera_ids)
            for c in ids - set(self._slots):
                self._slots[c] = _Slot()
                self._viewers.setdefault(c, 0)
            for c in set(self._slots) - ids:
                del self._slots[c]

    def set_disabled(self, camera_ids) -> None:
        """Cameras disabled in the DB (they may still run from --sourceN): /stream answers 404, open streams end."""
        with self._lock:
            self._disabled = set(camera_ids)

    def viewer_count(self, camera_id: int | None = None, raw: bool | None = None) -> int:
        with self._lock:
            return sum(n for (c, r), n in self._viewers.items()
                       if (camera_id is None or c == camera_id) and (raw is None or r == raw))

    def close(self) -> None:
        """Server shutdown: open streams end, the encoder threads stop."""
        self.closed.set()
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- web server side

    def can_stream(self, camera_id: int) -> bool:
        """Known (running) and not disabled in the DB."""
        with self._lock:
            return camera_id in self._slots and camera_id not in self._disabled

    def seq(self, camera_id: int) -> int:
        with self._lock:
            s = self._slots.get(camera_id)
            return 0 if s is None else s.seq

    def health(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._health)

    def _add_viewer(self, camera_id: int, raw: bool, n: int) -> int:
        with self._lock:
            k = (camera_id, raw)
            self._viewers[k] = self._viewers.get(k, 0) + n
            return self._viewers[k]

    def _cached(self, camera_id: int, raw: bool) -> tuple[int, bytes] | None:
        """The cached JPEG if it is of the newest frame (call with self._lock held)."""
        s = self._slots.get(camera_id)
        hit = s.jpegs.get(raw) if s is not None else None
        return hit if hit is not None and hit[0] == s.seq else None

    def _encode(self, camera_id: int, raw: bool = False) -> tuple[int, bytes | None]:
        """Resize (+ annotate unless raw) + JPEG of the newest frame, in an encoder thread; cached per frame."""
        with self._lock:
            hit = self._cached(camera_id, raw)
            if hit is not None:
                return hit
            s = self._slots.get(camera_id)
            if s is None or s.image is None:
                return 0, None
            seq, image, result, zones, lines, banner = s.seq, s.image, s.result, s.zones, list(s.lines), s.banner
        if raw:   # same size as the annotated stream, no drawings at all (also during a pause: the zone editor)
            img = resize_for_display(image, STREAM_WIDTH)[0]
        elif banner is not None:  # detection paused: no boxes or zones, just the banner
            img = draw_banner(resize_for_display(image, STREAM_WIDTH)[0], banner)
        else:
            img = annotate(image, result, zones, STREAM_WIDTH, lines)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
        if not ok:
            return seq, None
        data = buf.tobytes()
        with self._lock:
            self.encodes[kind(raw)] += 1
            s = self._slots.get(camera_id)
            if s is not None and seq >= s.jpegs.get(raw, (-1, b""))[0]:
                s.jpegs[raw] = (seq, data)
        return seq, data

    def _encoder_pool(self) -> ThreadPoolExecutor | None:
        with self._lock:
            if self._pool is None and not self.closed.is_set():
                self._pool = ThreadPoolExecutor(ENCODE_THREADS, thread_name_prefix="stream-encode")
            return self._pool

    async def jpeg(self, camera_id: int, raw: bool = False) -> tuple[int, bytes | None]:
        """JPEG of the newest frame, encoded once per frame and kind, whatever the number of viewers."""
        lock = self._enc_locks.setdefault((camera_id, raw), asyncio.Lock())
        async with lock:
            with self._lock:
                hit = self._cached(camera_id, raw)
            if hit is not None:
                return hit
            pool = self._encoder_pool()
            if pool is None:      # shutting down
                return 0, None
            try:
                return await asyncio.get_running_loop().run_in_executor(pool, self._encode, camera_id, raw)
            except RuntimeError:  # pool shut down between the check and the submit
                return 0, None


def _part(data: bytes) -> bytes:
    return f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data + b"\r\n"


async def _mjpeg(hub: FrameHub, camera_id: int, raw: bool):
    """One viewer's stream. Starlette cancels it when the client disconnects; the finally counts the viewer out."""
    n = hub._add_viewer(camera_id, raw, 1)
    log.debug("stream cam%d %s: viewer connected (%d watching)", camera_id, kind(raw), n)
    try:
        sent, next_send = 0, 0.0      # next_send 0: the newest frame goes out at once, then ~STREAM_FPS
        while not hub.closed.is_set() and hub.can_stream(camera_id):
            now = time.monotonic()
            if now >= next_send and hub.seq(camera_id) not in (0, sent):
                seq, data = await hub.jpeg(camera_id, raw)
                if data is not None and seq != sent:
                    sent, next_send = seq, now + 1.0 / STREAM_FPS
                    yield _part(data)
                    continue
            await asyncio.sleep(POLL_S)  # no frame yet (or not due yet): keep the connection open
    finally:
        n = hub._add_viewer(camera_id, raw, -1)
        log.debug("stream cam%d %s: viewer left (%d watching)", camera_id, kind(raw), n)


def create_app(hub: FrameHub, token: str, reload_event: threading.Event, crossing_check: bool = False) -> FastAPI:
    app = FastAPI(title="RADS worker")

    def check_token(x_rads_internal: str | None = Header(default=None, alias=INTERNAL_HEADER)) -> None:
        if not token or x_rads_internal != token:
            raise HTTPException(status_code=401, detail="bad or missing internal token")

    @app.get(PATH_WORKER_STREAM)
    async def stream(camera_id: int, raw: bool = False):
        if not hub.can_stream(camera_id):
            raise HTTPException(status_code=404, detail=f"camera {camera_id} is unknown or disabled")
        return StreamingResponse(_mjpeg(hub, camera_id, raw),
                                 media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                                 headers={"Cache-Control": "no-cache, no-store"})

    @app.post(PATH_WORKER_RELOAD, dependencies=[Depends(check_token)])
    def reload() -> ReloadResponse:
        reload_event.set()
        log.info("reload requested")
        return ReloadResponse(ok=True)

    @app.get("/health")
    def health() -> dict:
        h = hub.health()
        cams = {str(c): {**v, "viewers": hub.viewer_count(c), "raw_viewers": hub.viewer_count(c, raw=True)}
                for c, v in h.get("cameras", {}).items()}
        return {"time": now_iso(), "crossing_check": h.get("crossing_check", crossing_check),
                "open_incidents": sum(v.get("open_incidents", 0) for v in cams.values()), "cameras": cams}

    return app


class WebServer:
    """uvicorn in a daemon thread. Binds to 127.0.0.1 only. A busy port is logged; the worker carries on."""

    def __init__(self, hub: FrameHub, token: str, crossing_check: bool = False, host: str = WORKER_HOST,
                 port: int = WORKER_PORT) -> None:
        if host != LOCALHOST:
            raise ValueError(f"the worker web server listens on {LOCALHOST} only, not {host!r}")
        self.hub = hub
        self.port = port
        self.reload_event = threading.Event()
        self.app = create_app(hub, token, self.reload_event, crossing_check)
        self._server = uvicorn.Server(uvicorn.Config(self.app, host=host, port=port, log_level="warning",
                                                     log_config=None, lifespan="off", timeout_graceful_shutdown=2))
        self._thread = threading.Thread(target=self._serve, name="webserver", daemon=True)

    def _serve(self) -> None:
        try:
            self._server.run()
        except SystemExit:  # uvicorn exits when it cannot bind
            log.error("web server could not start on %s:%d (port in use?) – no live view or /reload",
                      LOCALHOST, self.port)
        except Exception:
            log.exception("web server failed")

    def start(self, timeout: float = 5.0) -> bool:
        self._thread.start()
        end = time.monotonic() + timeout
        while time.monotonic() < end and self._thread.is_alive() and not self._server.started:
            time.sleep(0.02)
        if self._server.started:
            log.info("web server on http://%s:%d (/stream/{camera_id}, /reload, /health)", LOCALHOST, self.port)
            return True
        log.error("web server did not start on %s:%d", LOCALHOST, self.port)
        return False

    def stop(self, timeout: float = 5.0) -> None:
        self.hub.close()               # open streams end, encoder threads stop
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)
