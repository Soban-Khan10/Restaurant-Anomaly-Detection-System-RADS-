"""/api/cameras CRUD (writes: Admin/Technical), live stream relay and snapshot (contract message 8).

Reading the camera list is allowed for every logged-in role (dashboard tiles), but `rtsp_url` (it contains the
camera password) is returned only to Admin and Technical users. Audit rows never contain the password either.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import status as http
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import diff, write_audit
from app.auth import ROLES_LIVE_VIEW, ROLES_SETTINGS
from app.deps import CurrentUser, get_current_user, get_current_user_header_or_query, get_db, require_role
from app.worker import WorkerClient, WorkerUnavailable, get_worker, reload_after_save
from app.ws import cameras as camera_monitor

log = logging.getLogger("rads.app.cameras")
router = APIRouter(prefix="/api/cameras", tags=["cameras"])
settings_role = require_role(*ROLES_SETTINGS)

URL_SCHEMES = ("rtsp", "rtsps", "http", "https")


# ---------------------------------------------------------------- models

def _check_url(v: str) -> str:
    v = v.strip()
    parts = urlsplit(v)
    if parts.scheme.lower() not in URL_SCHEMES or not parts.hostname:
        raise ValueError("must be a camera URL such as rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101")
    return v


class _FrameSize(BaseModel):
    @model_validator(mode="after")
    def _both_or_neither(self):
        w, h = getattr(self, "frame_width", None), getattr(self, "frame_height", None)
        fields = self.model_fields_set
        if ("frame_width" in fields) != ("frame_height" in fields) or (w is None) != (h is None):
            raise ValueError("set frame_width and frame_height together")
        return self


class CameraCreate(_FrameSize):
    name: str = Field(min_length=1, max_length=60)
    rtsp_url: str = Field(max_length=500)
    enabled: bool = True
    target_fps: float = Field(5, ge=1, le=30)
    frame_width: int | None = Field(None, ge=160, le=7680)
    frame_height: int | None = Field(None, ge=120, le=4320)

    _url = field_validator("rtsp_url")(_check_url)


class CameraUpdate(_FrameSize):
    name: str | None = Field(None, min_length=1, max_length=60)
    rtsp_url: str | None = Field(None, max_length=500)
    enabled: bool | None = None
    target_fps: float | None = Field(None, ge=1, le=30)
    frame_width: int | None = Field(None, ge=160, le=7680)
    frame_height: int | None = Field(None, ge=120, le=4320)

    @field_validator("rtsp_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return None if v is None else _check_url(v)


class CameraOut(BaseModel):
    camera_id: int
    name: str
    enabled: bool
    target_fps: float
    frame_width: int | None
    frame_height: int | None
    status: str = Field(description="live status from worker heartbeats: online / offline / reconnecting")
    fps: float | None


class CameraAdminOut(CameraOut):
    rtsp_url: str


# ---------------------------------------------------------------- helpers

def mask_rtsp(url: str | None) -> str | None:
    """rtsp://user:secret@host/... -> rtsp://user:***@host/... (for audit rows)."""
    if not url:
        return url
    p = urlsplit(url)
    if p.password is None:
        return url
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    return urlunsplit((p.scheme, f"{p.username}:***@{host}", p.path, p.query, p.fragment))


def _row(db: Session, camera_id: int) -> dict[str, Any]:
    row = db.execute(text("SELECT * FROM cameras WHERE camera_id = :id"), {"id": camera_id}).mappings().first()
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Camera not found")
    return dict(row)


def _out(row: dict[str, Any], user: CurrentUser) -> CameraOut:
    live = camera_monitor.status_of(row["camera_id"])
    data = {**row, "enabled": bool(row["enabled"]), "status": live.status.value, "fps": live.fps}
    if user.role in ROLES_SETTINGS:
        return CameraAdminOut(**data)
    data.pop("rtsp_url", None)  # Managers never see the camera password
    return CameraOut(**data)


def _audit_values(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in ("name", "rtsp_url", "enabled", "target_fps", "frame_width", "frame_height")}
    out["rtsp_url"] = mask_rtsp(out["rtsp_url"])
    if out["enabled"] is not None:
        out["enabled"] = bool(out["enabled"])
    return out


def _check_zones_fit(db: Session, camera_id: int, width: int | None, height: int | None) -> None:
    if width is None or height is None:
        return
    for z in db.execute(text("SELECT zone_id, name, polygon FROM zones WHERE camera_id = :c"), {"c": camera_id}):
        pts = json.loads(z.polygon)
        if any(not (0 <= x <= width and 0 <= y <= height) for x, y in pts):
            raise HTTPException(http.HTTP_409_CONFLICT,
                                f"Zone '{z.name}' (id {z.zone_id}) lies outside {width}x{height}; "
                                "edit the zone first")


# ---------------------------------------------------------------- CRUD

_DOC_RESPONSES = {200: {"model": CameraAdminOut,
                        "description": "rtsp_url is included for Admin and Technical users only"}}


@router.get("", response_model=None, responses={200: {"model": list[CameraAdminOut]}})
def list_cameras(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CameraOut]:
    rows = db.execute(text("SELECT * FROM cameras ORDER BY camera_id")).mappings().all()
    return [_out(dict(r), user) for r in rows]


@router.get("/{camera_id}", response_model=None, responses=_DOC_RESPONSES)
def get_camera(camera_id: int, user: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_db)) -> CameraOut:
    return _out(_row(db, camera_id), user)


@router.post("", response_model=None, status_code=http.HTTP_201_CREATED, responses={201: {"model": CameraAdminOut}})
def create_camera(body: CameraCreate, request: Request, response: Response,
                  user: CurrentUser = Depends(settings_role), db: Session = Depends(get_db),
                  worker: WorkerClient = Depends(get_worker)) -> CameraOut:
    camera_id = db.execute(
        text("INSERT INTO cameras (name, rtsp_url, enabled, target_fps, frame_width, frame_height) "
             "VALUES (:name, :rtsp_url, :enabled, :target_fps, :frame_width, :frame_height) RETURNING camera_id"),
        {**body.model_dump(), "enabled": int(body.enabled)}).scalar_one()
    row = _row(db, camera_id)
    write_audit(db, "camera_create", user_id=user.user_id, target_type="camera", target_id=camera_id,
                details={"new": _audit_values(row)}, request=request)
    db.commit()
    reload_after_save(worker, response)
    return _out(row, user)


@router.patch("/{camera_id}", response_model=None, responses=_DOC_RESPONSES)
def update_camera(camera_id: int, body: CameraUpdate, request: Request, response: Response,
                  user: CurrentUser = Depends(settings_role), db: Session = Depends(get_db),
                  worker: WorkerClient = Depends(get_worker)) -> CameraOut:
    old = _row(db, camera_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "rtsp_url", "enabled", "target_fps"):
        if required in changes and changes[required] is None:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"{required} cannot be null")
    if "frame_width" in changes:
        _check_zones_fit(db, camera_id, changes["frame_width"], changes["frame_height"])
    if changes:
        values = {k: int(v) if k == "enabled" else v for k, v in changes.items()}
        db.execute(text(f"UPDATE cameras SET {', '.join(f'{k} = :{k}' for k in values)} WHERE camera_id = :id"),
                   {**values, "id": camera_id})  # keys come from the pydantic model, not the client
    new = _row(db, camera_id)
    write_audit(db, "camera_update", user_id=user.user_id, target_type="camera", target_id=camera_id,
                details=diff(_audit_values(old), _audit_values(new)), request=request)
    db.commit()
    reload_after_save(worker, response)
    return _out(new, user)


@router.delete("/{camera_id}", status_code=http.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, request: Request, user: CurrentUser = Depends(settings_role),
                  db: Session = Depends(get_db), worker: WorkerClient = Depends(get_worker)) -> Response:
    old = _row(db, camera_id)
    has_history = db.execute(text(
        "SELECT (SELECT COUNT(*) FROM incidents WHERE camera_id = :c) + "
        "(SELECT COUNT(*) FROM camera_health WHERE camera_id = :c)"), {"c": camera_id}).scalar_one()
    if has_history:
        raise HTTPException(http.HTTP_409_CONFLICT,
                            "This camera has incidents or health history; disable it instead (enabled = false)")
    # Its own rules and zones go with it (rule_zones rows are removed by ON DELETE CASCADE).
    rules = db.execute(text("DELETE FROM rules WHERE camera_id = :c RETURNING rule_id"), {"c": camera_id}).all()
    zones = db.execute(text("DELETE FROM zones WHERE camera_id = :c RETURNING zone_id"), {"c": camera_id}).all()
    db.execute(text("DELETE FROM cameras WHERE camera_id = :c"), {"c": camera_id})
    write_audit(db, "camera_delete", user_id=user.user_id, target_type="camera", target_id=camera_id,
                details={"old": _audit_values(old), "deleted_rules": [r[0] for r in rules],
                         "deleted_zones": [z[0] for z in zones]}, request=request)
    db.commit()
    response = Response(status_code=http.HTTP_204_NO_CONTENT)
    reload_after_save(worker, response)
    return response


# ---------------------------------------------------------------- live view (message 8 relay)

def _require_camera(db: Session, camera_id: int) -> None:
    _row(db, camera_id)
    db.close()  # don't hold a DB connection for the life of a stream


@router.get("/{camera_id}/stream", response_class=StreamingResponse,
            responses={200: {"content": {"multipart/x-mixed-replace": {}}}, 503: {"description": "worker down"}})
async def camera_stream(camera_id: int, user: CurrentUser = Depends(get_current_user_header_or_query),
                        db: Session = Depends(get_db),
                        worker: WorkerClient = Depends(get_worker)) -> StreamingResponse:
    """Live view: the worker's MJPEG stream (~2 FPS, boxes and zones drawn), relayed byte for byte.
    Login required; accepts ?token=<jwt> so it works in an <img src=...> tag."""
    _check_live_view(user)
    _require_camera(db, camera_id)
    try:
        client, upstream = await worker.open_stream(camera_id)
    except WorkerUnavailable as e:
        raise _worker_error(e) from None

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except httpx.HTTPError as e:  # worker stopped mid-stream: end the picture, don't crash the request
            log.warning("live view of camera %s ended: worker stream broke (%s)", camera_id, type(e).__name__)
        finally:
            await worker.close_stream(client, upstream)

    media = upstream.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame")
    return StreamingResponse(relay(), media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/{camera_id}/snapshot", response_class=Response,
            responses={200: {"content": {"image/jpeg": {}}}, 503: {"description": "worker down"}})
async def camera_snapshot(camera_id: int, user: CurrentUser = Depends(get_current_user_header_or_query),
                          db: Session = Depends(get_db),
                          worker: WorkerClient = Depends(get_worker)) -> Response:
    """One JPEG frame from the worker's stream (used to draw zones on). Login required; accepts ?token=."""
    _check_live_view(user)
    _require_camera(db, camera_id)
    try:
        jpeg = await worker.snapshot(camera_id)
    except WorkerUnavailable as e:
        raise _worker_error(e) from None
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _check_live_view(user: CurrentUser) -> None:
    if user.role not in ROLES_LIVE_VIEW:  # Managers: open team question, one line in app/auth.py
        raise HTTPException(http.HTTP_403_FORBIDDEN, "Your role is not allowed to do this")


def _worker_error(e: WorkerUnavailable) -> HTTPException:
    if e.status_code == http.HTTP_404_NOT_FOUND:
        return HTTPException(http.HTTP_404_NOT_FOUND, "The worker has no stream for this camera (disabled?)")
    log.warning("live view unavailable: %s", e)
    return HTTPException(http.HTTP_503_SERVICE_UNAVAILABLE, "Live view unavailable: the worker is not running")
