"""GET /api/incidents, GET /api/incidents/{id}, PATCH /api/incidents/{id}/review.

The app changes only the review columns of `incidents` (status, reviewed_by, reviewed_at, review_note,
escalated_at). Inserting incidents and setting end_time / conf_* belongs to the worker.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.auth import ROLES_REVIEW
from app.deps import CurrentUser, get_current_user, get_db, require_role
from shared.contract import TZ, HazardId, IncidentStatus, Severity, now_iso

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


# ---------------------------------------------------------------- models

class IncidentOut(BaseModel):
    incident_id: int
    hazard_id: str
    hazard_name: str
    rule_id: int
    camera_id: int
    camera_name: str
    zone_id: int
    zone_name: str
    track_id: int | None
    start_time: str
    end_time: str | None
    conf_avg: float | None
    conf_max: float | None
    severity: str
    model_version: str
    status: str
    reviewed_by: int | None
    reviewed_at: str | None
    review_note: str | None
    escalated_at: str | None
    evidence_id: int | None = Field(description="first evidence row (snapshot), for thumbnails")


class IncidentPage(BaseModel):
    items: list[IncidentOut]
    total: int
    page: int
    page_size: int


class EvidenceOut(BaseModel):
    evidence_id: int
    kind: str
    file_path: str
    captured_at: str
    detections: Any
    deleted: bool
    deleted_at: str | None
    image_url: str


class IncidentDetail(IncidentOut):
    hazard: dict[str, Any]
    rule: dict[str, Any]
    camera: dict[str, Any]
    zone: dict[str, Any]
    evidence: list[EvidenceOut]
    alerts: list[dict[str, Any]]
    reviewed_by_user: dict[str, Any] | None


class ReviewIn(BaseModel):
    status: Literal["Acknowledged", "Confirmed violation", "False alarm"]
    note: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- queries

_SELECT = """
SELECT i.*, h.name AS hazard_name, c.name AS camera_name, z.name AS zone_name,
       (SELECT MIN(e.evidence_id) FROM evidence e WHERE e.incident_id = i.incident_id) AS evidence_id
FROM incidents i
JOIN hazards h ON h.hazard_id = i.hazard_id
JOIN cameras c ON c.camera_id = i.camera_id
JOIN zones   z ON z.zone_id   = i.zone_id
"""


def _parse_bound(value: str, *, end: bool) -> tuple[str, str]:
    """Return (operator, ISO +05:00 text). A date-only `end` bound includes that whole day."""
    try:
        if len(value) == 10:
            d = date.fromisoformat(value)
            if end:
                return "<", datetime.combine(d + timedelta(days=1), time(), TZ).isoformat(timespec="seconds")
            return ">=", datetime.combine(d, time(), TZ).isoformat(timespec="seconds")
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Bad date '{value}'; use YYYY-MM-DD or ISO 8601") from None
    dt = dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
    return ("<=" if end else ">="), dt.isoformat(timespec="seconds")


def load_incident(db: Session, incident_id: int) -> dict[str, Any]:
    row = db.execute(text(_SELECT + " WHERE i.incident_id = :id"), {"id": incident_id}).mappings().first()
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Incident not found")
    return dict(row)


def _json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value


def incident_detail(db: Session, incident_id: int) -> IncidentDetail:
    inc = load_incident(db, incident_id)
    one = lambda q, **p: dict(db.execute(text(q), p).mappings().first() or {})  # noqa: E731

    hazard = one("SELECT * FROM hazards WHERE hazard_id = :h", h=inc["hazard_id"])
    rule = one("SELECT * FROM rules WHERE rule_id = :r", r=inc["rule_id"])
    # Never rtsp_url: it contains the camera password.
    camera = one("SELECT camera_id, name, enabled, target_fps, frame_width, frame_height "
                 "FROM cameras WHERE camera_id = :c", c=inc["camera_id"])
    zone = one("SELECT * FROM zones WHERE zone_id = :z", z=inc["zone_id"])
    zone["polygon"] = _json(zone.get("polygon"))

    evidence = [
        EvidenceOut(evidence_id=e["evidence_id"], kind=e["kind"], file_path=e["file_path"],
                    captured_at=e["captured_at"], detections=_json(e["detections"]),
                    deleted=e["deleted_at"] is not None, deleted_at=e["deleted_at"],
                    image_url=f"/api/evidence/{e['evidence_id']}/image")
        for e in db.execute(text("SELECT * FROM evidence WHERE incident_id = :i ORDER BY evidence_id"),
                            {"i": incident_id}).mappings()
    ]
    alerts = [dict(a) for a in db.execute(
        text("SELECT * FROM alerts WHERE incident_id = :i ORDER BY alert_id"), {"i": incident_id}).mappings()]
    reviewer = None
    if inc["reviewed_by"] is not None:
        reviewer = one("SELECT user_id, username, full_name FROM users WHERE user_id = :u", u=inc["reviewed_by"])

    return IncidentDetail(**inc, hazard=hazard, rule=rule, camera=camera, zone=zone,
                          evidence=evidence, alerts=alerts, reviewed_by_user=reviewer or None)


# ---------------------------------------------------------------- routes

@router.get("", response_model=IncidentPage)
def list_incidents(
    date_from: str | None = Query(None, description="YYYY-MM-DD or ISO datetime (inclusive)"),
    date_to: str | None = Query(None, description="YYYY-MM-DD (whole day included) or ISO datetime"),
    camera_id: int | None = None,
    hazard_id: HazardId | None = None,
    severity: Severity | None = None,
    status: list[IncidentStatus] | None = Query(None, description="repeat for several, e.g. status=New&status=Acknowledged"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IncidentPage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if date_from:
        op, params["date_from"] = _parse_bound(date_from, end=False)
        where.append(f"i.start_time {op} :date_from")
    if date_to:
        op, params["date_to"] = _parse_bound(date_to, end=True)
        where.append(f"i.start_time {op} :date_to")
    if camera_id is not None:
        where.append("i.camera_id = :camera_id")
        params["camera_id"] = camera_id
    if hazard_id is not None:
        where.append("i.hazard_id = :hazard_id")
        params["hazard_id"] = hazard_id.value
    if severity is not None:
        where.append("i.severity = :severity")
        params["severity"] = severity.value
    if status:
        names = [f":st{n}" for n in range(len(status))]
        where.append(f"i.status IN ({', '.join(names)})")
        params.update({f"st{n}": s.value for n, s in enumerate(status)})

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(text("SELECT COUNT(*) FROM incidents i" + clause), params).scalar_one()
    rows = db.execute(
        text(_SELECT + clause + " ORDER BY i.start_time DESC, i.incident_id DESC LIMIT :limit OFFSET :offset"),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()
    return IncidentPage(items=[IncidentOut(**r) for r in rows], total=total, page=page, page_size=page_size)


class HazardCount(BaseModel):
    hazard_id: str
    name: str
    count: int


class IncidentSummary(BaseModel):
    date_from: date
    date_to: date
    total: int
    unreviewed: int
    by_hazard: list[HazardCount] = Field(description="every hazard, including those with 0 incidents")
    by_status: dict[str, int]


# Declared before /{incident_id}, which would otherwise try to read "summary" as an incident id.
@router.get("/summary", response_model=IncidentSummary)
def incident_summary(
    date_from: date | None = Query(None, description="first day (YYYY-MM-DD); default today (Pakistan time)"),
    date_to: date | None = Query(None, description="last day, included; default = date_from"),
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IncidentSummary:
    """Counts for the dashboard overview: incidents per hazard and per status in a day range."""
    start = date_from or datetime.now(TZ).date()
    end = date_to or start
    if end < start:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "date_to must not be before date_from")
    p = {"lo": datetime.combine(start, time(), TZ).isoformat(timespec="seconds"),
         "hi": datetime.combine(end + timedelta(days=1), time(), TZ).isoformat(timespec="seconds")}
    in_range = "i.start_time >= :lo AND i.start_time < :hi"
    by_hazard = [HazardCount(hazard_id=r[0], name=r[1], count=r[2]) for r in db.execute(text(
        f"SELECT h.hazard_id, h.name, COUNT(i.incident_id) FROM hazards h LEFT JOIN incidents i "
        f"ON i.hazard_id = h.hazard_id AND {in_range} GROUP BY h.hazard_id ORDER BY h.hazard_id"), p)]
    counts = dict(db.execute(text(f"SELECT i.status, COUNT(*) FROM incidents i WHERE {in_range} GROUP BY 1"), p).all())
    by_status = {s.value: counts.get(s.value, 0) for s in IncidentStatus}
    return IncidentSummary(date_from=start, date_to=end, total=sum(by_status.values()),
                           unreviewed=by_status[IncidentStatus.NEW.value], by_hazard=by_hazard, by_status=by_status)


@router.get("/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, _: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> IncidentDetail:
    return incident_detail(db, incident_id)


@router.patch("/{incident_id}/review", response_model=IncidentDetail)
def review_incident(incident_id: int, body: ReviewIn, request: Request,
                    user: CurrentUser = Depends(require_role(*ROLES_REVIEW)),
                    db: Session = Depends(get_db)) -> IncidentDetail:
    old = load_incident(db, incident_id)
    params: dict[str, Any] = {"st": body.status, "by": user.user_id, "at": now_iso(), "id": incident_id}
    note_sql = ""
    if body.note is not None:
        note_sql = ", review_note = :note"
        params["note"] = body.note
    db.execute(text("UPDATE incidents SET status = :st, reviewed_by = :by, reviewed_at = :at"
                    f"{note_sql} WHERE incident_id = :id"), params)
    write_audit(db, "incident_review", user_id=user.user_id, target_type="incident", target_id=incident_id,
                details={"status": {"old": old["status"], "new": body.status}, "note": body.note},
                request=request)
    db.commit()
    return incident_detail(db, incident_id)
