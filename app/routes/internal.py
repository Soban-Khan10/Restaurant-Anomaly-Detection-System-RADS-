"""/internal/* – worker -> app messages 2, 5, 6 (contract section 5).

127.0.0.1 only, header X-RADS-Internal required. The worker has already written the rows; these calls only tell the
app something changed (Phase 3 broadcasts them to dashboards over the WebSocket).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db, require_internal
from shared.contract import PATH_HEARTBEAT, PATH_INCIDENT_CLOSED, PATH_INCIDENT_NEW, Heartbeat, IncidentRef

log = logging.getLogger("rads.app.internal")
router = APIRouter(tags=["internal"], dependencies=[Depends(require_internal)])

# Latest heartbeat per camera, in memory only (the worker already wrote camera_health).
LAST_HEARTBEAT: dict[int, Heartbeat] = {}


class Ok(BaseModel):
    ok: bool = True


def _require_incident(db: Session, incident_id: int) -> None:
    if db.execute(text("SELECT 1 FROM incidents WHERE incident_id = :id"), {"id": incident_id}).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")


@router.post(PATH_INCIDENT_NEW, response_model=Ok)
def incident_new(body: IncidentRef, db: Session = Depends(get_db)) -> Ok:
    _require_incident(db, body.incident_id)
    log.info("incident %s opened", body.incident_id)
    return Ok()


@router.post(PATH_INCIDENT_CLOSED, response_model=Ok)
def incident_closed(body: IncidentRef, db: Session = Depends(get_db)) -> Ok:
    _require_incident(db, body.incident_id)
    log.info("incident %s closed", body.incident_id)
    return Ok()


@router.post(PATH_HEARTBEAT, response_model=Ok)
def heartbeat(body: Heartbeat) -> Ok:
    LAST_HEARTBEAT[body.camera_id] = body
    return Ok()
