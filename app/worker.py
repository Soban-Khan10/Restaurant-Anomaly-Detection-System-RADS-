"""Calls from the app to the pipeline worker (contract section 5, messages 7 and 8).

  POST {WORKER}/reload            -> {"ok": true}; after every camera/zone/rule save. Worker down = warning only.
  GET  {WORKER}/stream/{camera}   -> MJPEG (multipart/x-mixed-replace); relayed to logged-in users.
Both carry X-RADS-Internal: <RADS_INTERNAL_TOKEN>.
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from shared.config import get_settings
from shared.contract import INTERNAL_HEADER, PATH_WORKER_RELOAD, PATH_WORKER_STREAM

log = logging.getLogger("rads.app.worker")

RELOAD_TIMEOUT_S = 3.0
STREAM_CONNECT_TIMEOUT_S = 3.0
SNAPSHOT_TIMEOUT_S = 5.0
SNAPSHOT_MAX_BYTES = 10 * 1024 * 1024


class WorkerUnavailable(Exception):
    """Worker not running, not reachable, or answered with an error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class WorkerClient:
    def __init__(self, base_url: str, token: str,
                 transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {INTERNAL_HEADER: token}
        self.transport = transport  # tests pass httpx.MockTransport or httpx.ASGITransport

    # ---------------------------------------------------------------- message 7
    def reload(self) -> bool:
        """Ask the worker to re-read settings. Never raises; False (and a warning) if the worker is down."""
        try:
            with httpx.Client(base_url=self.base_url, transport=self.transport, timeout=RELOAD_TIMEOUT_S) as c:
                r = c.post(PATH_WORKER_RELOAD, json={}, headers=self.headers)
            if r.is_success:
                return True
            log.warning("worker /reload answered %s; settings were saved, worker will pick them up later",
                        r.status_code)
        except httpx.HTTPError as e:
            log.warning("worker /reload failed (%s: %s); settings were saved, worker will pick them up "
                        "when it starts", type(e).__name__, e)
        return False

    # ---------------------------------------------------------------- message 8
    async def open_stream(self, camera_id: int) -> tuple[httpx.AsyncClient, httpx.Response]:
        """Open the worker's MJPEG stream. Caller must close both (see close_stream)."""
        client = httpx.AsyncClient(base_url=self.base_url, transport=self.transport,
                                   timeout=httpx.Timeout(STREAM_CONNECT_TIMEOUT_S, read=None))
        try:
            req = client.build_request("GET", PATH_WORKER_STREAM.format(camera_id=camera_id), headers=self.headers)
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            await client.aclose()
            raise WorkerUnavailable(f"worker not reachable: {type(e).__name__}") from e
        if not resp.is_success:
            await resp.aclose()
            await client.aclose()
            raise WorkerUnavailable(f"worker answered {resp.status_code}", resp.status_code)
        return client, resp

    @staticmethod
    async def close_stream(client: httpx.AsyncClient, resp: httpx.Response) -> None:
        await resp.aclose()
        await client.aclose()

    async def snapshot(self, camera_id: int) -> bytes:
        """First complete JPEG frame from the worker's MJPEG stream."""
        client, resp = await self.open_stream(camera_id)
        try:
            return await asyncio.wait_for(first_jpeg(resp), SNAPSHOT_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise WorkerUnavailable("no frame from the worker within 5 s") from None
        finally:
            await self.close_stream(client, resp)


_CONTENT_LENGTH = re.compile(rb"content-length:\s*(\d+)", re.IGNORECASE)


def extract_jpeg(buf: bytes) -> bytes | None:
    """One JPEG from the start of an MJPEG byte stream, or None if more bytes are needed.

    Uses the part's Content-Length header when present; otherwise the JPEG start/end markers.
    """
    start = buf.find(b"\xff\xd8")
    if start < 0:
        return None
    m = None
    head_end = buf.find(b"\r\n\r\n")
    if 0 <= head_end < start:
        m = _CONTENT_LENGTH.search(buf, 0, head_end)
    if m:
        n = int(m.group(1))
        return buf[start:start + n] if len(buf) >= start + n else None
    end = buf.find(b"\xff\xd9", start + 2)
    return buf[start:end + 2] if end >= 0 else None


async def first_jpeg(resp: httpx.Response) -> bytes:
    buf = b""
    async for chunk in resp.aiter_bytes():
        buf += chunk
        frame = extract_jpeg(buf)
        if frame is not None:
            return frame
        if len(buf) > SNAPSHOT_MAX_BYTES:
            break
    raise WorkerUnavailable("worker stream ended without a complete JPEG frame")


def get_worker() -> WorkerClient:
    """FastAPI dependency (overridden in tests)."""
    s = get_settings()
    return WorkerClient(s.worker_url, s.internal_token)


RELOAD_HEADER = "X-RADS-Worker-Reload"


def reload_after_save(worker: WorkerClient, response) -> bool:
    """Call after a settings change is committed. Sets X-RADS-Worker-Reload: ok|failed on the response so the
    dashboard can say "saved, but the worker is not running"."""
    ok = worker.reload()
    response.headers[RELOAD_HEADER] = "ok" if ok else "failed"
    return ok
