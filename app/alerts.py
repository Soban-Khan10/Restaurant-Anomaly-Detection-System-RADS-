"""Alerts: dashboard alert rows, WhatsApp / Telegram messages, and the 10-minute escalation job.

* Every new incident  -> one `alerts` row (channel dashboard, kind first), then a short text to every active
  Manager over each configured channel.
* Escalation (every minute) -> incident still `New` 10 min after start and `escalated_at` empty: set escalated_at,
  `alerts` row (dashboard, kind escalation), text to every active Admin.
* Catch-up (at startup, then every minute, before escalation) -> an incident the worker reported while the app was
  down never got its first alert: write the dashboard row now; text Managers only if it started within the last
  hour (older ones are left to escalation, so a long outage does not flood phones).
* Each external send gets its own `alerts` row: `sent`, or `failed` with the error. A failed send never raises.

Channels (configured in .env; nothing configured -> dashboard only):
  Email     SMTP_HOST + SMTP_USER + SMTP_PASSWORD + SMTP_FROM      -> each recipient's users.email (app.email_alerts)
  WhatsApp  WHATSAPP_TOKEN + WHATSAPP_PHONE_ID (Meta Cloud API) -> each recipient's users.phone
  Telegram  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID               -> that chat (e.g. a staff group); a bot cannot
            message a phone number, so recipients are chat ids, not users. TELEGRAM_CHAT_ID may hold several ids
            separated by commas. (Supported, but Telegram is blocked in Pakistan, so it is left off.)

Never twice: an incident gets at most one first and one escalation message per channel and recipient, whichever
path sends it (message 2, the catch-up, a restart). No external messages for incidents that started while
monitoring was paused on their camera.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.email_alerts import NO_RECIPIENTS, EmailSender, email_config
from app.log_redaction import mask_address
from shared.config import Settings, get_settings
from shared.contract import TZ, AlertChannel, AlertKind, AlertStatus, IncidentStatus, RoleName, now_iso

log = logging.getLogger("rads.app.alerts")

ESCALATE_AFTER = timedelta(minutes=10)
CATCH_UP_GRACE = timedelta(seconds=60)        # newer incidents are still being reported by the worker (message 2)
CATCH_UP_TEXT_WITHIN = timedelta(minutes=60)  # older incidents get the dashboard row only (catch-up and escalation)
HTTP_TIMEOUT_S = 10.0
WHATSAPP_API = "https://graph.facebook.com/v21.0"
TELEGRAM_API = "https://api.telegram.org"
ERROR_MAX = 500


# ---------------------------------------------------------------- senders

@dataclass(frozen=True)
class Recipient:
    user_id: int | None
    address: str


class Sender(Protocol):
    channel: AlertChannel

    def recipients(self, users: list[dict[str, Any]]) -> list[Recipient]: ...
    def send(self, address: str, message: Any) -> None: ...  # raises on failure
    # optional: compose(incident, kind) -> the message for send(); otherwise a short text (format_message)


class SendError(Exception):
    pass


def _post(client: httpx.Client, url: str, secret: str, **kw: Any) -> None:
    """POST and raise SendError with a message that never contains the secret (Telegram puts it in the URL)."""
    try:
        r = client.post(url, timeout=HTTP_TIMEOUT_S, **kw)
    except httpx.HTTPError as e:
        raise SendError(f"{type(e).__name__}: {e}".replace(secret, "***")) from None
    if not r.is_success:
        raise SendError(f"HTTP {r.status_code}: {r.text[:300]}".replace(secret, "***"))


class WhatsAppSender:
    """Meta WhatsApp Cloud API. Note: free-form text is only delivered inside a 24 h conversation window;
    outside it Meta requires an approved template (the error is recorded in alerts.error)."""
    channel = AlertChannel.WHATSAPP

    def __init__(self, token: str, phone_id: str, client: httpx.Client | None = None):
        self.token, self.phone_id = token, phone_id
        self.client = client or httpx.Client()

    def recipients(self, users: list[dict[str, Any]]) -> list[Recipient]:
        out = []
        for u in users:
            digits = re.sub(r"\D", "", u.get("phone") or "")
            if digits:
                out.append(Recipient(u["user_id"], digits))
        return out

    def send(self, address: str, message: str) -> None:
        _post(self.client, f"{WHATSAPP_API}/{self.phone_id}/messages", self.token,
              headers={"Authorization": f"Bearer {self.token}"},
              json={"messaging_product": "whatsapp", "to": address, "type": "text",
                    "text": {"preview_url": False, "body": message}})


class TelegramSender:
    channel = AlertChannel.TELEGRAM

    def __init__(self, bot_token: str, chat_ids: list[str], client: httpx.Client | None = None):
        self.bot_token, self.chat_ids = bot_token, chat_ids
        self.client = client or httpx.Client()

    def recipients(self, users: list[dict[str, Any]]) -> list[Recipient]:
        return [Recipient(None, c) for c in self.chat_ids]

    def send(self, address: str, message: str) -> None:
        _post(self.client, f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage", self.bot_token,
              json={"chat_id": address, "text": message})


def configured_senders(settings: Settings | None = None, telegram_chat_id: str | None = None) -> list[Sender]:
    s = settings or get_settings()
    chat = telegram_chat_id if telegram_chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
    senders: list[Sender] = []
    email = email_config()
    if email.enabled:
        senders.append(EmailSender(email))
    if s.whatsapp_token and s.whatsapp_phone_id:
        senders.append(WhatsAppSender(s.whatsapp_token, s.whatsapp_phone_id))
    if s.telegram_bot_token:
        chat_ids = [c.strip() for c in chat.split(",") if c.strip()]
        if chat_ids:
            senders.append(TelegramSender(s.telegram_bot_token, chat_ids))
        else:
            log.warning("TELEGRAM_BOT_TOKEN is set but TELEGRAM_CHAT_ID is empty; Telegram alerts are off")
    return senders


# ---------------------------------------------------------------- DB helpers

def _incident_summary(db: Session, incident_id: int) -> dict[str, Any] | None:
    row = db.execute(text(
        "SELECT i.incident_id, i.hazard_id, h.name AS hazard_name, h.tied_to, c.name AS camera_name, "
        "z.name AS zone_name, i.start_time, i.severity, i.status, i.track_id, i.conf_avg, i.conf_max, "
        "(SELECT e.file_path FROM evidence e WHERE e.incident_id = i.incident_id AND e.deleted_at IS NULL "
        " ORDER BY e.evidence_id LIMIT 1) AS evidence_path "
        "FROM incidents i JOIN hazards h ON h.hazard_id = i.hazard_id "
        "JOIN cameras c ON c.camera_id = i.camera_id JOIN zones z ON z.zone_id = i.zone_id "
        "WHERE i.incident_id = :id"), {"id": incident_id}).mappings().first()
    return dict(row) if row else None


def _users_with_role(db: Session, roles: Iterable[RoleName]) -> list[dict[str, Any]]:
    names = [r.value for r in roles]
    params = {f"r{i}": n for i, n in enumerate(names)}
    return [dict(r) for r in db.execute(text(
        "SELECT u.user_id, u.username, u.phone, u.email FROM users u JOIN roles r ON r.role_id = u.role_id "
        f"WHERE u.active = 1 AND r.name IN ({', '.join(':' + k for k in params)}) ORDER BY u.user_id"),
        params).mappings()]


def _insert_alert(db: Session, incident_id: int, channel: AlertChannel, kind: AlertKind, status: AlertStatus,
                  user_id: int | None = None, error: str | None = None) -> None:
    db.execute(text("INSERT INTO alerts (incident_id, user_id, channel, kind, sent_at, status, error) "
                    "VALUES (:i, :u, :c, :k, :t, :s, :e)"),
               {"i": incident_id, "u": user_id, "c": channel.value, "k": kind.value, "t": now_iso(),
                "s": status.value, "e": error[:ERROR_MAX] if error else None})


def _already_sent(db: Session, incident_id: int, channel: AlertChannel, kind: AlertKind, user_id: int) -> bool:
    return db.execute(text("SELECT 1 FROM alerts WHERE incident_id = :i AND channel = :c AND kind = :k "
                           "AND user_id = :u AND status = 'sent'"),
                      {"i": incident_id, "c": channel.value, "k": kind.value, "u": user_id}).first() is not None


def _recorded(db: Session, incident_id: int, channel: AlertChannel, kind: AlertKind, error: str) -> bool:
    return db.execute(text("SELECT 1 FROM alerts WHERE incident_id = :i AND channel = :c AND kind = :k "
                           "AND user_id IS NULL AND error = :e"),
                      {"i": incident_id, "c": channel.value, "k": kind.value, "e": error}).first() is not None


def format_message(inc: dict[str, Any], kind: AlertKind) -> str:
    when = datetime.fromisoformat(inc["start_time"]).astimezone(TZ).strftime("%H:%M")
    head = "RADS ESCALATION – not reviewed for 10 min" if kind == AlertKind.ESCALATION else "RADS alert"
    return (f"{head}: {inc['hazard_id']} {inc['hazard_name']} at {inc['camera_name']} / {inc['zone_name']}, "
            f"{when} ({inc['severity']}). Incident #{inc['incident_id']} – please check the dashboard.")


# ---------------------------------------------------------------- first alert (called from /internal/incidents/new)

def record_dashboard_alert(db: Session, incident_id: int) -> bool:
    """Write the dashboard alert row for a new incident. Returns False if it already exists
    (the worker retried message 2), so a retry never produces duplicate alerts or texts."""
    exists = db.execute(text("SELECT 1 FROM alerts WHERE incident_id = :i AND channel = 'dashboard' "
                             "AND kind = 'first'"), {"i": incident_id}).first()
    if exists:
        return False
    _insert_alert(db, incident_id, AlertChannel.DASHBOARD, AlertKind.FIRST, AlertStatus.SENT)
    db.commit()
    return True


def send_external(Session_: sessionmaker[Session], incident_id: int, kind: AlertKind,
                  roles: Iterable[RoleName], senders: list[Sender] | None = None) -> int:
    """Text every active user with one of `roles` over every configured channel, one alerts row per send.
    Never raises. Returns the number of successful sends."""
    try:
        senders = configured_senders() if senders is None else senders
        if not senders:
            return 0
        if _during_pause(Session_, incident_id, also_now=False):
            log.warning("incident %s started during a monitoring pause: no %s messages", incident_id, kind.value)
            return 0
        with Session_() as db:
            inc = _incident_summary(db, incident_id)
            if inc is None:
                return 0
            users = _users_with_role(db, roles)
            sent = 0
            for sender in senders:
                rcpts = sender.recipients(users)
                if not rcpts and sender.channel == AlertChannel.EMAIL:
                    if not _recorded(db, incident_id, sender.channel, kind, NO_RECIPIENTS):
                        log.warning("email %s alert for incident %s: %s", kind.value, incident_id, NO_RECIPIENTS)
                        _insert_alert(db, incident_id, sender.channel, kind, AlertStatus.FAILED, None, NO_RECIPIENTS)
                        db.commit()
                    continue
                compose = getattr(sender, "compose", None)
                message = compose(inc, kind) if compose else format_message(inc, kind)
                for rcpt in rcpts:
                    if rcpt.user_id is not None and _already_sent(db, incident_id, sender.channel, kind,
                                                                  rcpt.user_id):
                        continue  # never the same alert twice to the same person
                    try:
                        sender.send(rcpt.address, message)
                    except Exception as e:  # noqa: BLE001 – a failed send never breaks anything else
                        error = (str(e) or type(e).__name__).replace(rcpt.address, mask_address(rcpt.address))
                        who = f"user #{rcpt.user_id}" if rcpt.user_id is not None else "the group chat"
                        log.warning("%s alert for incident %s to %s failed: %s", sender.channel.value, incident_id,
                                    who, error)
                        _insert_alert(db, incident_id, sender.channel, kind, AlertStatus.FAILED, rcpt.user_id, error)
                    else:
                        sent += 1
                        _insert_alert(db, incident_id, sender.channel, kind, AlertStatus.SENT, rcpt.user_id)
                    db.commit()
            return sent
    except Exception:  # noqa: BLE001
        log.exception("sending %s alerts for incident %s failed", kind.value, incident_id)
        return 0


def send_first_alerts(Session_: sessionmaker[Session], incident_id: int, senders: list[Sender] | None = None) -> int:
    return send_external(Session_, incident_id, AlertKind.FIRST, (RoleName.MANAGER,), senders)


# ---------------------------------------------------------------- catch-up for missed first alerts

def _started_within(start_time: str, now: datetime, span: timedelta) -> bool:
    try:
        start = datetime.fromisoformat(start_time)
    except (TypeError, ValueError):
        return False
    start = start.replace(tzinfo=TZ) if start.tzinfo is None else start
    return now - start <= span


def _during_pause(Session_: sessionmaker[Session], incident_id: int, also_now: bool) -> bool:
    """True if the incident started while monitoring was paused on its camera (no texts for those)."""
    from app.pauses import incident_in_pause  # noqa: PLC0415
    with Session_() as db:
        row = db.execute(text("SELECT camera_id, start_time FROM incidents WHERE incident_id = :i"),
                         {"i": incident_id}).first()
        return row is not None and incident_in_pause(db, row[0], row[1], also_now=also_now) is not None


def catch_up_first_alerts(Session_: sessionmaker[Session], senders: list[Sender] | None = None,
                          now: datetime | None = None) -> list[int]:
    """First alerts for incidents the app never heard about (it was down when the worker sent message 2).

    Every incident still `New` with no alerts row of kind `first`, and older than CATCH_UP_GRACE, gets its dashboard
    alert now. WhatsApp/Telegram go out only for those that started within CATCH_UP_TEXT_WITHIN. Safe to repeat: an
    incident with a first alert is never picked again, and record_dashboard_alert() refuses a second dashboard row
    (also when message 2 arrives late). Returns the incident ids that got their dashboard alert now."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    with Session_() as db:
        missed = db.execute(text(
            "SELECT i.incident_id, i.start_time FROM incidents i WHERE i.status = :new AND i.start_time <= :grace "
            "AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.incident_id = i.incident_id AND a.kind = :first) "
            "ORDER BY i.incident_id"),
            {"new": IncidentStatus.NEW.value, "first": AlertKind.FIRST.value,
             "grace": (now - CATCH_UP_GRACE).isoformat(timespec="seconds")}).all()
        caught = [(iid, start) for iid, start in missed if record_dashboard_alert(db, iid)]
    if not caught:
        return []
    recent = [iid for iid, start in caught if _started_within(start, now, CATCH_UP_TEXT_WITHIN)
              and not _during_pause(Session_, iid, also_now=False)]
    ids = [iid for iid, _ in caught]
    shown = ", ".join(map(str, ids[:20])) + (f" … (+{len(ids) - 20} more)" if len(ids) > 20 else "")
    log.warning("first alert caught up for %d incident(s) reported while the app was down: %s; "
                "WhatsApp/Telegram for %d of them (started within 60 min)", len(ids), shown, len(recent))
    for iid in recent:
        send_first_alerts(Session_, iid, senders)
    return [iid for iid, _ in caught]


def run_minute_jobs(Session_: sessionmaker[Session], senders: list[Sender] | None = None,
                    now: datetime | None = None) -> None:
    """The 1-minute job (also run once at startup): catch up missed first alerts, then escalate."""
    try:
        catch_up_first_alerts(Session_, senders, now)
    except Exception:  # noqa: BLE001 – escalation must still run
        log.exception("catching up missed first alerts failed")
    run_escalation(Session_, senders, now)


# ---------------------------------------------------------------- escalation job (every minute)

def run_escalation(Session_: sessionmaker[Session], senders: list[Sender] | None = None,
                   now: datetime | None = None) -> list[int]:
    """Escalate incidents still `New` ESCALATE_AFTER after start. Returns the escalated incident ids.

    Every escalated incident gets its dashboard escalation row. Email/WhatsApp/Telegram go out only for incidents that
    started within CATCH_UP_TEXT_WITHIN (60 min), the same rule as the catch-up: after the app server or the computer
    was off, old incidents must not flood the Admins' inboxes."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    cutoff = (now - ESCALATE_AFTER).isoformat(timespec="seconds")
    escalated: list[int] = []
    with Session_() as db:
        rows = db.execute(
            text("SELECT incident_id, start_time FROM incidents WHERE status = :new AND escalated_at IS NULL "
                 "AND start_time <= :cutoff ORDER BY incident_id"),
            {"new": IncidentStatus.NEW.value, "cutoff": cutoff}).all()
        starts = {iid: start for iid, start in rows}
        for iid in starts:
            # Conditional update: never escalate twice, and skip if it was reviewed in the meantime.
            res = db.execute(text("UPDATE incidents SET escalated_at = :t WHERE incident_id = :i "
                                  "AND escalated_at IS NULL AND status = :new"),
                             {"t": now.isoformat(timespec="seconds"), "i": iid, "new": IncidentStatus.NEW.value})
            if res.rowcount == 1:
                _insert_alert(db, iid, AlertChannel.DASHBOARD, AlertKind.ESCALATION, AlertStatus.SENT)
                escalated.append(iid)
        db.commit()
    for iid in escalated:
        log.warning("incident %s escalated to Admins (still New after %s min)", iid,
                    int(ESCALATE_AFTER.total_seconds() // 60))
        if _during_pause(Session_, iid, also_now=False):
            log.warning("incident %s happened during a monitoring pause: escalation shown on the dashboard only", iid)
            continue
        if not _started_within(starts[iid], now, CATCH_UP_TEXT_WITHIN):
            log.warning("incident %s started more than %d min ago (the app server was off or asleep): escalation "
                        "shown on the dashboard only, no email/WhatsApp/Telegram", iid,
                        int(CATCH_UP_TEXT_WITHIN.total_seconds() // 60))
            continue
        send_external(Session_, iid, AlertKind.ESCALATION, (RoleName.ADMIN,), senders)
    return escalated
