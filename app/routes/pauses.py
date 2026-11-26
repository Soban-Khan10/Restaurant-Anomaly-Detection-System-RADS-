"""Pause monitoring: POST /api/pauses, POST /api/pauses/{id}/end, GET /api/pauses, GET /api/monitoring/status.

Every role may pause and resume (ROLES_PAUSE). After each change the worker is asked to reload (if it is down the
change is still saved, with a warning) and every dashboard gets a monitoring.changed WebSocket event.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import pauses
from app.audit import write_audit
from app.auth import ROLES_PAUSE
from app.deps import CurrentUser, get_current_user, get_db, require_role
from app.worker import WorkerClient, get_worker, reload_after_save
from app.ws import hub
from shared.contract import TZ, PauseReason, now_iso

router = APIRouter(tags=["pauses"])
pause_role = require_role(*ROLES_PAUSE)
State = Literal["active", "planned", "ended", "cancelled"]


class PauseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_id: int | None = Field(None, description="null = all cameras")
    reason: PauseReason
    note: str | None = Field(None, max_length=500, description="required when the reason is 'other'")
    starts_at: datetime | None = Field(None, description="default now; without an offset it is Pakistan time")
    ends_at: datetime | None = Field(None, description="give ends_at or duration_minutes, or neither = until resumed")
    duration_minutes: int | None = Field(None, ge=1, le=7 * 24 * 60)


class PauseOut(BaseModel):
    pause_id: int
    camera_id: int | None
    camera_name: str
    reason: PauseReason
    reason_label: str
    note: str | None
    starts_at: str
    ends_at: str | None
    created_by: int
    created_by_name: str | None
    created_at: str
    ended_by: int | None
    ended_by_name: str | None
    ended_at: str | None
    state: State


class CameraMonitoring(BaseModel):
    camera_id: int
    camera_name: str
    enabled: bool
    state: Literal["active", "paused", "planned"]
    pause: PauseOut | None = Field(description="the pause in force (state paused)")
    next_pause: PauseOut | None = Field(description="the next planned pause covering this camera")


def _bad(msg: str) -> HTTPException:
    return HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, msg)


def _create(db: Session, body: PauseCreate, user: CurrentUser, request: Request) -> dict[str, Any]:
    now = datetime.now(TZ)
    note = (body.note or "").strip() or None
    if body.reason == PauseReason.OTHER and not note:
        raise _bad("Write a short note when the reason is Other.")
    if body.camera_id is not None and db.execute(text("SELECT 1 FROM cameras WHERE camera_id = :c"),
                                                 {"c": body.camera_id}).first() is None:
        raise _bad(f"Camera {body.camera_id} does not exist.")
    start = pauses.local(body.starts_at) if body.starts_at else now
    if start < now - pauses.PAST_TOLERANCE:
        raise _bad("A pause cannot start in the past.")
    start = max(start, now)
    if body.ends_at is not None and body.duration_minutes is not None:
        raise _bad("Give either an end time or a duration, not both.")
    end = (pauses.local(body.ends_at) if body.ends_at else
           start + timedelta(minutes=body.duration_minutes) if body.duration_minutes else None)
    if end is not None and end <= start:
        raise _bad("The end must be after the start.")
    if end is not None and end - start > pauses.MAX_LENGTH:
        raise _bad("A pause can last at most 7 days.")
    other = pauses.find_overlap(db, body.camera_id, start, end, now)
    if other is not None:
        raise HTTPException(http.HTTP_409_CONFLICT, pauses.overlap_message(other, now))

    pause_id = db.execute(text(
        "INSERT INTO monitoring_pauses (camera_id, reason, note, starts_at, ends_at, created_by, created_at) "
        "VALUES (:c, :r, :n, :s, :e, :u, :t) RETURNING pause_id"),
        {"c": body.camera_id, "r": body.reason.value, "n": note, "s": pauses.iso(start),
         "e": pauses.iso(end) if end else None, "u": user.user_id, "t": now_iso()}).scalar_one()
    write_audit(db, "pause_create", user_id=user.user_id, target_type="pause", target_id=pause_id,
                details={"camera_id": body.camera_id, "reason": body.reason.value, "note": note,
                         "starts_at": pauses.iso(start), "ends_at": pauses.iso(end) if end else None},
                request=request)
    db.commit()
    return pauses.describe(pauses.load(db, pause_id), now)


def _end(db: Session, pause_id: int, user: CurrentUser, request: Request) -> dict[str, Any]:
    now = datetime.now(TZ)
    row = pauses.load(db, pause_id)
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Pause not found")
    before = pauses.state_of(row, now)
    if before in ("ended", "cancelled"):
        raise HTTPException(http.HTTP_409_CONFLICT, "This pause has already ended.")
    db.execute(text("UPDATE monitoring_pauses SET ended_by = :u, ended_at = :t WHERE pause_id = :id"),
               {"u": user.user_id, "t": pauses.iso(now), "id": pause_id})
    write_audit(db, "pause_end", user_id=user.user_id, target_type="pause", target_id=pause_id,
                details={"action": "cancelled" if before == "planned" else "resumed",
                         "camera_id": row["camera_id"], "reason": row["reason"]}, request=request)
    db.commit()
    return pauses.describe(pauses.load(db, pause_id), now)


@router.post("/api/pauses", response_model=PauseOut, status_code=http.HTTP_201_CREATED)
async def create_pause(body: PauseCreate, request: Request, response: Response,
                       user: CurrentUser = Depends(pause_role), db: Session = Depends(get_db),
                       worker: WorkerClient = Depends(get_worker)) -> dict[str, Any]:
    """Pause detection on one camera (or all) now or later, for a time or until resumed."""
    out = await run_in_threadpool(_create, db, body, user, request)
    await run_in_threadpool(reload_after_save, worker, response)
    await hub.broadcast(pauses.changed_message("created", out["pause_id"], out["camera_id"]))
    return out


@router.post("/api/pauses/{pause_id}/end", response_model=PauseOut)
async def end_pause(pause_id: int, request: Request, response: Response,
                    user: CurrentUser = Depends(pause_role), db: Session = Depends(get_db),
                    worker: WorkerClient = Depends(get_worker)) -> dict[str, Any]:
    """Resume detection now (active pause), or cancel a planned one."""
    out = await run_in_threadpool(_end, db, pause_id, user, request)
    await run_in_threadpool(reload_after_save, worker, response)
    await hub.broadcast(pauses.changed_message(out["state"], out["pause_id"], out["camera_id"]))
    return out


@router.get("/api/pauses", response_model=list[PauseOut])
def list_pauses(active: bool | None = Query(None, description="true: only pauses in force now; false: all others"),
                state: State | None = None,
                camera_id: int | None = Query(None, description="also includes all-cameras pauses"),
                date_from: date | None = Query(None, alias="from", description="YYYY-MM-DD, Pakistan time"),
                date_to: date | None = Query(None, alias="to", description="YYYY-MM-DD, included"),
                _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Pause history and planned pauses, newest first."""
    now = datetime.now(TZ)
    lo = datetime.combine(date_from, time(), TZ) if date_from else None
    hi = datetime.combine(date_to + timedelta(days=1), time(), TZ) if date_to else None
    out = []
    for row in pauses.all_pauses(db):
        d = pauses.describe(row, now)
        if active is not None and (d["state"] == "active") != active:
            continue
        if state is not None and d["state"] != state:
            continue
        if camera_id is not None and row["camera_id"] not in (None, camera_id):
            continue
        start = pauses.when(row["starts_at"])
        end = start if d["state"] == "cancelled" else pauses.effective_end(row)
        if hi is not None and start >= hi:
            continue
        if lo is not None and end is not None and (end < lo if d["state"] == "cancelled" else end <= lo):
            continue
        out.append(d)
    return out[:500]


@router.get("/api/monitoring/status", response_model=list[CameraMonitoring])
def monitoring_status(_: CurrentUser = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Per camera: detection active, paused (reason, until, by whom), or a pause planned (the next one)."""
    return pauses.monitoring_status(db, datetime.now(TZ))
