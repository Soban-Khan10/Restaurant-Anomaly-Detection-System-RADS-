"""audit_log helper. Every login, evidence view, rule/zone/user change and incident review calls write_audit().

The row is added to the caller's session; the caller commits it together with the change it describes.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.contract import now_iso


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def write_audit(
    db: Session,
    action: str,
    *,
    user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    db.execute(
        text("INSERT INTO audit_log (user_id, action, target_type, target_id, details, ip_address, created_at) "
             "VALUES (:u, :a, :tt, :ti, :d, :ip, :t)"),
        {"u": user_id, "a": action, "tt": target_type, "ti": target_id,
         "d": json.dumps(details, default=str) if details is not None else None,
         "ip": client_ip(request), "t": now_iso()},
    )


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{field: {"old": ..., "new": ...}} for fields whose value changed (for rule/zone/user updates)."""
    return {k: {"old": old.get(k), "new": v} for k, v in new.items() if old.get(k) != v}
