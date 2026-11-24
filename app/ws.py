"""WebSocket hub: /ws?token=<jwt>  (contract section 5, message 3).

Broadcasts to every logged-in dashboard:
  {"type": "incident.new",    "incident": {...row...}}
  {"type": "incident.closed", "incident_id": 1042}
  {"type": "camera.status",   "camera_id": 1, "status": "online", "fps": 4.9, "ts": "..."}

Camera status: every worker heartbeat is forwarded as camera.status. If a camera sends no heartbeat for
CAMERA_OFFLINE_AFTER_S (15 s), the app broadcasts camera.status "offline" for it once. The app writes nothing
to camera_health (the worker owns it); this state lives in memory only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db, user_from_token
from shared.contract import CAMERA_OFFLINE_AFTER_S, CameraStatus, Heartbeat, WsCameraStatus, now_iso

log = logging.getLogger("rads.app.ws")
router = APIRouter(tags=["websocket"])

SEND_TIMEOUT_S = 2.0


class Hub:
    """The set of connected dashboards. Slow or broken connections are dropped, never block a broadcast."""

    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        self.connections.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def _send(self, ws: WebSocket, message: dict[str, Any]) -> WebSocket | None:
        try:
            await asyncio.wait_for(ws.send_json(message), SEND_TIMEOUT_S)
            return None
        except Exception:  # noqa: BLE001  – disconnected, timed out, or closed mid-send
            return ws

    async def broadcast(self, message: dict[str, Any]) -> int:
        """Send to every connection; returns how many received it."""
        conns = list(self.connections)
        failed = [ws for ws in await asyncio.gather(*(self._send(ws, message) for ws in conns)) if ws is not None]
        for ws in failed:
            self.remove(ws)
        return len(conns) - len(failed)


class CameraMonitor:
    """Latest heartbeat per camera + offline detection (no heartbeat for CAMERA_OFFLINE_AFTER_S)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, offline_after: float = CAMERA_OFFLINE_AFTER_S):
        self.clock = clock
        self.offline_after = offline_after
        self.last: dict[int, tuple[Heartbeat, float]] = {}  # camera_id -> (heartbeat, received at)
        self.marked_offline: set[int] = set()

    def record(self, hb: Heartbeat) -> WsCameraStatus:
        self.last[hb.camera_id] = (hb, self.clock())
        self.marked_offline.discard(hb.camera_id)
        return WsCameraStatus(**hb.model_dump())

    def newly_offline(self) -> list[WsCameraStatus]:
        """Cameras whose last heartbeat is too old and which were not reported offline yet."""
        now = self.clock()
        out = []
        for cam, (_, received) in sorted(self.last.items()):
            if cam not in self.marked_offline and now - received > self.offline_after:
                self.marked_offline.add(cam)
                out.append(self._offline(cam))
        return out

    def status_of(self, camera_id: int) -> WsCameraStatus:
        entry = self.last.get(camera_id)
        if entry is None or camera_id in self.marked_offline or self.clock() - entry[1] > self.offline_after:
            return self._offline(camera_id)
        return WsCameraStatus(**entry[0].model_dump())

    @staticmethod
    def _offline(camera_id: int) -> WsCameraStatus:
        return WsCameraStatus(camera_id=camera_id, status=CameraStatus.OFFLINE, fps=None, ts=now_iso())


hub = Hub()
cameras = CameraMonitor()


async def check_offline(h: Hub | None = None, m: CameraMonitor | None = None) -> int:
    """One pass of offline detection; broadcasts camera.status offline for newly silent cameras."""
    h, m = h or hub, m or cameras
    stale = m.newly_offline()
    for msg in stale:
        log.warning("camera %s: no heartbeat for %ss, marked offline", msg.camera_id, int(m.offline_after))
        await h.broadcast(msg.model_dump(mode="json"))
    return len(stale)


async def offline_watch(interval: float = 1.0) -> None:
    """Background task started by app.main's lifespan."""
    while True:
        try:
            await check_offline()
        except Exception:  # noqa: BLE001 – never let the watcher die
            log.exception("offline check failed")
        await asyncio.sleep(interval)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(""), db: Session = Depends(get_db)) -> None:
    user = user_from_token(db, token) if token else None
    camera_ids = [r[0] for r in db.execute(text("SELECT camera_id FROM cameras WHERE enabled = 1 ORDER BY camera_id"))]
    db.close()  # don't hold a DB connection for the life of the socket
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="login required")
        return

    await websocket.accept()
    # Current state first, so a freshly opened dashboard knows every camera's status at once.
    for cam in camera_ids:
        await websocket.send_json(cameras.status_of(cam).model_dump(mode="json"))
    hub.add(websocket)
    log.info("dashboard connected: %s (%d open)", user.username, len(hub.connections))
    try:
        while True:
            msg = await websocket.receive_text()  # dashboards don't need to send anything; "ping" -> "pong"
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(websocket)
        log.info("dashboard disconnected: %s (%d open)", user.username, len(hub.connections))
