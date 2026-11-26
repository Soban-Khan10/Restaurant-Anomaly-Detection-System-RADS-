"""Pause monitoring, app side: what a pause looks like to people, overlap checks, per-camera status, and the
start / end transitions the 1-minute job announces.

"Is camera X paused now?" is always answered by shared.contract.active_pause(), the rule the worker uses too.
While a camera is paused the worker detects nothing there; live view keeps working.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from shared.contract import TZ, PauseReason, active_pause

log = logging.getLogger("rads.app.pauses")

MAX_LENGTH = timedelta(days=7)
PAST_TOLERANCE = timedelta(minutes=5)   # "now" from a slow browser may be a little in the past
ALL_CAMERAS = "All cameras"

_SELECT = """
SELECT p.*, c.name AS camera_name,
       COALESCE(cu.full_name, cu.username) AS created_by_name,
       COALESCE(eu.full_name, eu.username) AS ended_by_name
FROM monitoring_pauses p
LEFT JOIN cameras c ON c.camera_id = p.camera_id
LEFT JOIN users cu ON cu.user_id = p.created_by
LEFT JOIN users eu ON eu.user_id = p.ended_by
"""


# ---------------------------------------------------------------- times and states

def when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def local(dt: datetime) -> datetime:
    """An aware datetime in Pakistan time; a naive one is taken as Pakistan time."""
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def effective_end(row: dict[str, Any]) -> datetime | None:
    """When the pause really stops: its planned end, or earlier if it was ended by hand. None = until resumed."""
    ends = [t for t in (when(row["ends_at"]), when(row["ended_at"])) if t is not None]
    return min(ends) if ends else None


def state_of(row: dict[str, Any], now: datetime) -> str:
    """planned / active / ended / cancelled (ended by hand before it started; ending in its first second counts
    as ended, not cancelled)."""
    start, ended, end = when(row["starts_at"]), when(row["ended_at"]), when(row["ends_at"])
    if ended is not None and start is not None and ended < start:
        return "cancelled"
    if ended is not None or (end is not None and end <= now):
        return "ended"
    if start is not None and start > now:
        return "planned"
    return "active"


def describe(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    out = dict(row)
    out["camera_name"] = ALL_CAMERAS if row["camera_id"] is None else row.get("camera_name")
    out["reason_label"] = PauseReason(row["reason"]).label
    out["state"] = state_of(row, now)
    return out


def load(db: Session, pause_id: int) -> dict[str, Any] | None:
    row = db.execute(text(_SELECT + " WHERE p.pause_id = :id"), {"id": pause_id}).mappings().first()
    return dict(row) if row else None


def all_pauses(db: Session) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(_SELECT + " ORDER BY p.starts_at DESC, p.pause_id DESC")).mappings()]


def short(dt: datetime | None, ref: datetime) -> str:
    """'16:00' today, '26 Nov 16:00' on another day."""
    if dt is None:
        return ""
    dt = dt.astimezone(TZ)
    return f"{dt:%H:%M}" if dt.date() == ref.astimezone(TZ).date() else f"{dt:%d %b %H:%M}"


def span_text(row: dict[str, Any], now: datetime) -> str:
    start, end = when(row["starts_at"]), effective_end(row)
    until = f"until {short(end, now)}" if end else "until resumed"
    return (f"from {short(start, now)} {until}") if start and start > now else until


# ---------------------------------------------------------------- overlap

def _affects_same_camera(a: int | None, b: int | None) -> bool:
    return a is None or b is None or a == b


def find_overlap(db: Session, camera_id: int | None, start: datetime, end: datetime | None,
                 now: datetime) -> dict[str, Any] | None:
    """An existing pause that covers the same camera (an all-cameras pause covers every camera) at some moment
    of [start, end). Cancelled and finished pauses never conflict. Touching ends (one ends at 16:00, the next
    starts at 16:00) are allowed."""
    for row in all_pauses(db):
        if not _affects_same_camera(camera_id, row["camera_id"]) or state_of(row, now) == "cancelled":
            continue
        s, e = when(row["starts_at"]), effective_end(row)
        if s is None:
            continue
        if start < (e or datetime.max.replace(tzinfo=TZ)) and s < (end or datetime.max.replace(tzinfo=TZ)):
            return describe(row, now)
    return None


def overlap_message(other: dict[str, Any], now: datetime) -> str:
    """e.g. 'Camera 2 is already paused (Cleaning) until 16:00. End or cancel that pause first, ...'"""
    subject = ALL_CAMERAS if other["camera_id"] is None else other["camera_name"]
    if other["state"] == "planned":
        verb = "will be paused"
    else:
        verb = "are already paused" if other["camera_id"] is None else "is already paused"
    return (f"{subject} {verb} ({other['reason_label']}) {span_text(other, now)}. "
            "End or cancel that pause first, or choose another time.")


# ---------------------------------------------------------------- status per camera

def monitoring_status(db: Session, now: datetime) -> list[dict[str, Any]]:
    """Every camera: active, paused (the pause in force), or planned (the next pause that will cover it)."""
    cams = db.execute(text("SELECT camera_id, name, enabled FROM cameras ORDER BY camera_id")).mappings().all()
    rows = all_pauses(db)
    out = []
    for cam in cams:
        current = active_pause(db, cam["camera_id"], now)
        pause = describe(load(db, current["pause_id"]), now) if current else None
        upcoming = sorted((r for r in rows if _affects_same_camera(cam["camera_id"], r["camera_id"])
                           and state_of(r, now) == "planned"), key=lambda r: (when(r["starts_at"]), r["pause_id"]))
        nxt = describe(upcoming[0], now) if upcoming else None
        out.append({"camera_id": cam["camera_id"], "camera_name": cam["name"], "enabled": bool(cam["enabled"]),
                    "state": "paused" if pause else "planned" if nxt else "active",
                    "pause": pause, "next_pause": nxt})
    return out


def incident_in_pause(db: Session, camera_id: int, start_time: str, also_now: bool = True) -> dict[str, Any] | None:
    """The pause the incident falls in: at its start, or (also_now) at the moment it reaches the app – the race
    at the start of a pause. None if it was not during a pause."""
    start = when(start_time)
    found = active_pause(db, camera_id, start) if start else None
    if found is None and also_now:
        found = active_pause(db, camera_id)
    return found


# ---------------------------------------------------------------- dashboard event

def changed_message(change: str, pause_id: int | None = None, camera_id: int | None = None) -> dict[str, Any]:
    """WebSocket event: dashboards reload /api/monitoring/status when they get it."""
    return {"type": "monitoring.changed", "change": change, "pause_id": pause_id, "camera_id": camera_id}


# ---------------------------------------------------------------- start / end transitions (1-minute job)

class TransitionWatch:
    """Remembers when it last looked, and reports planned pauses that started and timed pauses that ran out since."""

    def __init__(self) -> None:
        self.last: datetime | None = None

    def check(self, db: Session, now: datetime) -> list[dict[str, Any]]:
        last, self.last = self.last, now
        if last is None:  # first run after startup: dashboards load the current state anyway
            return []
        events = []
        for row in db.execute(text("SELECT pause_id, camera_id, starts_at, ends_at FROM monitoring_pauses "
                                   "WHERE ended_at IS NULL")).mappings():
            s, e = when(row["starts_at"]), when(row["ends_at"])
            if s is not None and last < s <= now:
                events.append(changed_message("started", row["pause_id"], row["camera_id"]))
            if e is not None and last < e <= now:
                events.append(changed_message("finished", row["pause_id"], row["camera_id"]))
        return events


watch = TransitionWatch()
_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Called at startup: the scheduler thread hands its WebSocket messages to this loop."""
    global _loop
    _loop = loop


def broadcast_from_thread(message: dict[str, Any]) -> None:
    from app.ws import hub  # noqa: PLC0415 – the hub lives in the event loop's module
    if _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(hub.broadcast(message), _loop)


def run_transition_job(Session_: sessionmaker[Session], worker=None, notify: Callable[[dict], None] | None = None,
                       now: datetime | None = None) -> list[dict[str, Any]]:
    """Every minute: if a planned pause started or a timed one ended, ask the worker to reload once and tell
    every dashboard (monitoring.changed), so screens update without a refresh."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    with Session_() as db:
        events = watch.check(db, now)
    if not events:
        return []
    if worker is None:
        from app.worker import get_worker  # noqa: PLC0415
        worker = get_worker()
    worker.reload()
    for ev in events:
        log.info("pause %s %s (camera %s)", ev["pause_id"], ev["change"], ev["camera_id"] or "all")
        (notify or broadcast_from_thread)(ev)
    return events
