"""/internal/* – worker -> app messages 2, 5, 6 (contract section 5).

127.0.0.1 only, header X-RADS-Internal required. The worker has already written the rows; these calls tell the
app something changed, and the app forwards it to dashboards over the WebSocket (message 3).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app import alerts
from app.deps import get_db, require_internal
from app.routes.incidents import IncidentOut, load_incident
from app.ws import cameras, hub
from shared.contract import (PATH_HEARTBEAT, PATH_INCIDENT_CLOSED, PATH_INCIDENT_NEW, Heartbeat, IncidentRef,
                             WsIncidentClosed, WsIncidentNew)

log = logging.getLogger("rads.app.internal")
router = APIRouter(tags=["internal"], dependencies=[Depends(require_internal)])


class Ok(BaseModel):
    ok: bool = True


def _open_incident(db: Session, incident_id: int) -> tuple[dict, bool]:
    row = IncidentOut(**load_incident(db, incident_id)).model_dump()  # 404 if missing
    created = alerts.record_dashboard_alert(db, incident_id)
    return row, created


@router.post(PATH_INCIDENT_NEW, response_model=Ok)
async def incident_new(body: IncidentRef, background: BackgroundTasks, db: Session = Depends(get_db)) -> Ok:
    row, created = await run_in_threadpool(_open_incident, db, body.incident_id)
    n = await hub.broadcast(WsIncidentNew(incident=row).model_dump(mode="json"))
    log.info("incident %s opened (%s %s), sent to %d dashboard(s)", body.incident_id, row["hazard_id"],
             row["camera_name"], n)
    if created:  # a retried message 2 does not text anyone twice
        # After the response, so a slow WhatsApp/Telegram API never delays the worker. Own session for the task.
        background.add_task(alerts.send_first_alerts, sessionmaker(bind=db.get_bind()), body.incident_id)
    return Ok()


@router.post(PATH_INCIDENT_CLOSED, response_model=Ok)
async def incident_closed(body: IncidentRef, db: Session = Depends(get_db)) -> Ok:
    await run_in_threadpool(load_incident, db, body.incident_id)  # 404 if missing
    n = await hub.broadcast(WsIncidentClosed(incident_id=body.incident_id).model_dump(mode="json"))
    log.info("incident %s closed, sent to %d dashboard(s)", body.incident_id, n)
    return Ok()


@router.post(PATH_HEARTBEAT, response_model=Ok)
async def heartbeat(body: Heartbeat) -> Ok:
    # The worker already wrote camera_health; the app writes nothing and only forwards.
    await hub.broadcast(cameras.record(body).model_dump(mode="json"))
    return Ok()
