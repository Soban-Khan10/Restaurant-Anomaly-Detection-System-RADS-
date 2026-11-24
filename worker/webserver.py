"""Worker web server (contract messages 7 and 8): FastAPI + uvicorn in a thread on 127.0.0.1:8001 only.

    GET  /stream/{camera_id}  MJPEG (multipart/x-mixed-replace) of the latest annotated frame, ~2 FPS, 1344 px, q70
    POST /reload              X-RADS-Internal token; answers {"ok": true} at once and only sets a flag
    GET  /health              per camera status / fps / last frame, open incidents, crossing check (no token)

The main loop hands frames over through FrameHub.publish(): references only, no copy, no drawing, no encoding.
Drawing and JPEG encoding happen here, once per new frame and shared by all viewers of a camera, and only while
that camera has at least one /stream viewer – with no viewers the cost is ~0.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from shared.contract import (INTERNAL_HEADER, PATH_WORKER_RELOAD, PATH_WORKER_STREAM, STREAM_FPS, WORKER_HOST,
                             WORKER_PORT, ReloadResponse, now_iso)
from worker.draw import annotate

log = logging.getLogger(__name__)

STREAM_WIDTH = 1344
STREAM_JPEG_QUALITY = 70
BOUNDARY = "frame"
POLL_S = 0.05
LOCALHOST = "127.0.0.1"


@dataclass
class _Slot:
    seq: int = 0              # bumped by every publish
    image: Any = None
    result: Any = None
    zones: Any = ()
    lines: Any = ()
    jpeg_seq: int = -1        # seq the cached JPEG was made from
    jpeg: bytes | None = None


class FrameHub:
    """Hand-off point between the main loop (publish, set_health) and the web server (stream, health)."""

    def __init__(self, camera_ids=()) -> None:
        self._lock = threading.Lock()
        self._slots: dict[int, _Slot] = {c: _Slot() for c in camera_ids}
        self._viewers: dict[int, int] = {c: 0 for c in camera_ids}
        self._health: dict[str, Any] = {}
        self._enc_locks: dict[int, asyncio.Lock] = {}
        self.encodes = 0
        self.closed = threading.Event()

    # ---------------------------------------------------------------- main loop side (never waits on I/O)

    def publish(self, camera_id: int, image, result, zones, lines: list[str]) -> None:
        """Store references to the newest frame and its results. Callers must not modify them afterwards."""
        with self._lock:
            s = self._slots.get(camera_id)
            if s is not None:
                s.image, s.result, s.zones, s.lines = image, result, zones, lines
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

    def viewer_count(self, camera_id: int | None = None) -> int:
        with self._lock:
            return sum(self._viewers.values()) if camera_id is None else self._viewers.get(camera_id, 0)

    # ---------------------------------------------------------------- web server side

    def has_camera(self, camera_id: int) -> bool:
        with self._lock:
            return camera_id in self._slots

    def seq(self, camera_id: int) -> int:
        with self._lock:
            s = self._slots.get(camera_id)
            return 0 if s is None else s.seq

    def health(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._health)

    def _add_viewer(self, camera_id: int, n: int) -> None:
        with self._lock:
            self._viewers[camera_id] = self._viewers.get(camera_id, 0) + n

    def _encode(self, camera_id: int) -> tuple[int, bytes | None]:
        """Annotate + resize + JPEG of the newest frame (in a server worker thread), cached per frame."""
        with self._lock:
            s = self._slots.get(camera_id)
            if s is None or s.image is None:
                return 0, None
            if s.jpeg_seq == s.seq:
                return s.seq, s.jpeg
            seq, image, result, zones, lines = s.seq, s.image, s.result, s.zones, list(s.lines)
        img = annotate(image, result, zones, STREAM_WIDTH, lines)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
        if not ok:
            return seq, None
        data = buf.tobytes()
        with self._lock:
            self.encodes += 1
            s = self._slots.get(camera_id)
            if s is not None and seq >= s.jpeg_seq:
                s.jpeg_seq, s.jpeg = seq, data
        return seq, data

    async def jpeg(self, camera_id: int) -> tuple[int, bytes | None]:
        lock = self._enc_locks.setdefault(camera_id, asyncio.Lock())
        async with lock:  # one encode per new frame, whatever the number of viewers
            with self._lock:
                s = self._slots.get(camera_id)
                if s is not None and s.jpeg is not None and s.jpeg_seq == s.seq:
                    return s.seq, s.jpeg
            return await asyncio.to_thread(self._encode, camera_id)


async def _mjpeg(hub: FrameHub, camera_id: int):
    hub._add_viewer(camera_id, 1)
    log.info("stream cam%d: viewer connected (%d watching)", camera_id, hub.viewer_count(camera_id))
    try:
        sent, next_send = 0, 0.0
        while not hub.closed.is_set() and hub.has_camera(camera_id):
            now = time.monotonic()
            if now >= next_send and hub.seq(camera_id) not in (0, sent):
                seq, data = await hub.jpeg(camera_id)
                if data is not None and seq != sent:
                    sent, next_send = seq, now + 1.0 / STREAM_FPS
                    yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n"
                           .encode() + data + b"\r\n")
                    continue
            await asyncio.sleep(POLL_S)
    finally:
        hub._add_viewer(camera_id, -1)
        log.info("stream cam%d: viewer left (%d watching)", camera_id, hub.viewer_count(camera_id))


def create_app(hub: FrameHub, token: str, reload_event: threading.Event, crossing_check: bool = False) -> FastAPI:
    app = FastAPI(title="RADS worker")

    def check_token(x_rads_internal: str | None = Header(default=None, alias=INTERNAL_HEADER)) -> None:
        if not token or x_rads_internal != token:
            raise HTTPException(status_code=401, detail="bad or missing internal token")

    @app.get(PATH_WORKER_STREAM)
    async def stream(camera_id: int):
        if not hub.has_camera(camera_id):
            raise HTTPException(status_code=404, detail=f"unknown camera {camera_id}")
        return StreamingResponse(_mjpeg(hub, camera_id), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                                 headers={"Cache-Control": "no-cache, no-store"})

    @app.post(PATH_WORKER_RELOAD, dependencies=[Depends(check_token)])
    def reload() -> ReloadResponse:
        reload_event.set()
        log.info("reload requested")
        return ReloadResponse(ok=True)

    @app.get("/health")
    def health() -> dict:
        h = hub.health()
        cams = {str(c): {**v, "viewers": hub.viewer_count(c)} for c, v in h.get("cameras", {}).items()}
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
        self.hub.closed.set()          # open streams end
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)
