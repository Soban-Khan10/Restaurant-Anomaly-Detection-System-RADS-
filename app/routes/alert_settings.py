"""Admin: is email alerting set up, and does it work?

GET  /api/alerts/status      email on/off, SMTP host and user (never the password), recipients, last results
POST /api/alerts/test-email  sends "RADS test email – alerts are working" (to the admin, or to a given address)
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import alerts
from app.audit import write_audit
from app.deps import CurrentUser, get_db, require_role
from app.log_redaction import mask_address
from app.email_alerts import EmailError, EmailSender, email_config, test_content, valid_email
from shared.contract import RoleName, now_iso

log = logging.getLogger("rads.app.email")
router = APIRouter(prefix="/api/alerts", tags=["alerts"])
admin_only = require_role(RoleName.ADMIN)

# The last test email (kept in memory: test emails belong to no incident, so they have no alerts row).
last_test: dict[str, Any] | None = None


class TestEmailIn(BaseModel):
    to: str | None = Field(None, max_length=254, description="default: your own email address")


class TestEmailOut(BaseModel):
    status: Literal["sent", "failed"]
    to: str | None
    reason: str | None = Field(description="plain-language reason when it failed")


class EmailStatus(BaseModel):
    enabled: bool
    missing: list[str] = Field(description="settings still empty in .env")
    host: str | None
    port: int
    user: str | None
    sender: str | None
    starttls: bool
    dashboard_url: str | None
    attach_snapshot: bool
    managers_with_email: int
    admins_with_email: int
    last_alert: dict[str, Any] | None
    last_test: dict[str, Any] | None


class AlertStatus(BaseModel):
    email: EmailStatus
    whatsapp: bool
    telegram: bool


def _count(db: Session, role: str) -> int:
    return db.execute(text(
        "SELECT COUNT(*) FROM users u JOIN roles r ON r.role_id = u.role_id WHERE r.name = :r AND u.active = 1 "
        "AND u.email IS NOT NULL AND TRIM(u.email) != ''"), {"r": role}).scalar_one()


@router.get("/status", response_model=AlertStatus)
def alert_status(_: CurrentUser = Depends(admin_only), db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = email_config()
    last = db.execute(text(
        "SELECT a.alert_id, a.incident_id, a.kind, a.sent_at, a.status, a.error, "
        "COALESCE(u.full_name, u.username) AS recipient FROM alerts a LEFT JOIN users u ON u.user_id = a.user_id "
        "WHERE a.channel = 'email' ORDER BY a.alert_id DESC LIMIT 1")).mappings().first()
    channels = {s.channel.value for s in alerts.configured_senders()}
    return {"email": {"enabled": cfg.enabled, "missing": cfg.missing, "host": cfg.host or None, "port": cfg.port,
                      "user": cfg.user or None, "sender": cfg.sender or None, "starttls": cfg.starttls,
                      "dashboard_url": cfg.dashboard_url or None, "attach_snapshot": cfg.attach_snapshot,
                      "managers_with_email": _count(db, "Manager"), "admins_with_email": _count(db, "Admin"),
                      "last_alert": dict(last) if last else None, "last_test": last_test},
            "whatsapp": "whatsapp" in channels, "telegram": "telegram" in channels}


def _send_test(to: str, by: str) -> str | None:
    """Returns None when sent, else the plain reason."""
    cfg = email_config()
    if not cfg.enabled:
        return f"Email is off: set {', '.join(cfg.missing)} in .env and restart the server."
    try:
        EmailSender(cfg).send(to, test_content(cfg, by))
    except EmailError as e:
        return str(e)
    return None


@router.post("/test-email", response_model=TestEmailOut)
async def test_email(request: Request, body: TestEmailIn | None = None, user: CurrentUser = Depends(admin_only),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    global last_test
    to = (body.to or "").strip() if body else ""
    own = not to
    if not to:
        to = (db.execute(text("SELECT email FROM users WHERE user_id = :u"), {"u": user.user_id}).scalar() or "").strip()
        if not to:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Add your email address on the Users page first, or type an address to send to.")
    if not valid_email(to):
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a valid email address.")
    by = user.full_name or user.username
    reason = await run_in_threadpool(_send_test, to, by)
    result = {"status": "failed" if reason else "sent", "to": to, "reason": reason}
    last_test = {**result, "at": now_iso(), "by": by}
    # never the address itself in the log (privacy): the admin's own one is "user #<id>", a typed one is masked
    where = f"user #{user.user_id}" if own else mask_address(to)
    log.info("test email by user #%s to %s: %s%s", user.user_id, where, result["status"],
             f" ({reason})" if reason else "")
    write_audit(db, "test_email", user_id=user.user_id, details={"to": to, "status": result["status"],
                                                                "reason": reason}, request=request)
    db.commit()
    return result

