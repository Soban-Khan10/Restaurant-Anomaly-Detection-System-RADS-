"""Small read-only lists for the dashboard: GET /api/hazards, GET /api/alerts (any logged-in user)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import CurrentUser, get_current_user, get_db

router = APIRouter(tags=["lookups"])


class HazardOut(BaseModel):
    hazard_id: str
    name: str
    tied_to: str


class AlertOut(BaseModel):
    alert_id: int
    incident_id: int
    channel: str
    kind: str
    sent_at: str
    status: str
    hazard_id: str
    hazard_name: str
    camera_id: int
    camera_name: str
    zone_name: str
    severity: str
    start_time: str
    end_time: str | None
    incident_status: str


@router.get("/api/hazards", response_model=list[HazardOut])
def list_hazards(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[HazardOut]:
    rows = db.execute(text("SELECT hazard_id, name, tied_to FROM hazards ORDER BY hazard_id")).mappings().all()
    return [HazardOut(**r) for r in rows]


@router.get("/api/alerts", response_model=list[AlertOut])
def latest_alerts(
    limit: int = Query(20, ge=1, le=200),
    channel: Literal["dashboard", "whatsapp", "telegram"] = Query("dashboard"),
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AlertOut]:
    """Latest alerts, newest first, with what the incident was about. Default: the dashboard alerts
    (one per new incident, plus one per escalation)."""
    rows = db.execute(text(
        "SELECT a.alert_id, a.incident_id, a.channel, a.kind, a.sent_at, a.status, i.hazard_id, "
        "h.name AS hazard_name, i.camera_id, c.name AS camera_name, z.name AS zone_name, i.severity, "
        "i.start_time, i.end_time, i.status AS incident_status FROM alerts a "
        "JOIN incidents i ON i.incident_id = a.incident_id JOIN hazards h ON h.hazard_id = i.hazard_id "
        "JOIN cameras c ON c.camera_id = i.camera_id JOIN zones z ON z.zone_id = i.zone_id "
        "WHERE a.channel = :ch ORDER BY a.sent_at DESC, a.alert_id DESC LIMIT :n"),
        {"ch": channel, "n": limit}).mappings().all()
    return [AlertOut(**r) for r in rows]
