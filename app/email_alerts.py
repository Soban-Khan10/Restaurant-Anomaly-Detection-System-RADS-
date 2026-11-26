"""E-mail alerts over SMTP (e.g. Gmail with an app password).

Settings come from .env (see .env.example). E-mail is ON only when SMTP_HOST, SMTP_USER, SMTP_PASSWORD and SMTP_FROM
are all set. The password is only ever handed to smtplib.login(): it is never logged, stored or put into an error
text (and app.log_redaction hides it from every log line, just in case).

EmailSender fits app.alerts' sender interface: recipients() picks users with an email address, compose() builds the
message for one incident, send() delivers it to one address with up to 3 attempts. Errors are plain sentences for the
alerts table and the dashboard, e.g. "Gmail rejected the login – check the app password".
"""
from __future__ import annotations

import html
import logging
import os
import re
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from app.log_redaction import mask_address
from shared.contract import TZ, AlertChannel, AlertKind

log = logging.getLogger("rads.app.email")

SMTP_TIMEOUT_S = 15
ATTEMPTS = 3
RETRY_WAIT_S = (2, 5)  # before the 2nd and 3rd attempt
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]{2,}$")
FOOTER = "Automatic alert from RADS. Detections can be wrong; a person must review every incident."
NO_RECIPIENTS = "no recipients with an email address"


def valid_email(value: str) -> bool:
    return len(value) <= 254 and bool(EMAIL_RE.match(value))


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)  # shared.config has already loaded .env into the environment
    if " #" in value:
        value = value.split(" #", 1)[0]
    return value.strip()


def _true(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str           # SMTP_FROM
    starttls: bool
    dashboard_url: str    # RADS_DASHBOARD_URL, for the "Open incident" button ('' = no button)
    attach_snapshot: bool
    evidence_dir: Path | None = None

    @property
    def missing(self) -> list[str]:
        return [name for name, v in (("SMTP_HOST", self.host), ("SMTP_USER", self.user),
                                     ("SMTP_PASSWORD", self.password), ("SMTP_FROM", self.sender)) if not v]

    @property
    def enabled(self) -> bool:
        return not self.missing

    @property
    def provider(self) -> str:
        return "Gmail" if "gmail" in self.host.lower() or "googlemail" in self.host.lower() else "The mail server"

    def __repr__(self) -> str:  # never show the password, e.g. in a traceback or a debugger
        return f"EmailConfig(host={self.host!r}, port={self.port}, user={self.user!r}, password='***')"


def email_config() -> EmailConfig:
    from shared.config import get_settings  # noqa: PLC0415
    try:
        port = int(_env("SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    host, password = _env("SMTP_HOST"), _env("SMTP_PASSWORD")
    if "gmail" in host.lower():
        password = password.replace(" ", "")  # Gmail shows app passwords as "abcd efgh ijkl mnop"
    return EmailConfig(host=host, port=port, user=_env("SMTP_USER"), password=password,
                       sender=_env("SMTP_FROM"), starttls=_true(_env("SMTP_STARTTLS", "true") or "true"),
                       dashboard_url=_env("RADS_DASHBOARD_URL").rstrip("/"),
                       attach_snapshot=_true(_env("EMAIL_ATTACH_SNAPSHOT", "false")),
                       evidence_dir=get_settings().evidence_dir)


# ---------------------------------------------------------------- content

@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str
    attachment: tuple[str, bytes] | None = None  # (file name, JPEG bytes)


def _when(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return (dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt).astimezone(TZ)


def _pct(v: float | None) -> str | None:
    return None if v is None else f"{round(v * 100)}%"


def incident_lines(inc: dict[str, Any]) -> list[tuple[str, str]]:
    worker = "—" if inc.get("tied_to") == "zone" or inc.get("track_id") is None else f"#{inc['track_id']}"
    avg, top = _pct(inc.get("conf_avg")), _pct(inc.get("conf_max"))
    confidence = "—" if avg is None and top is None else f"average {avg or '—'}, highest {top or '—'}"
    return [
        ("Hazard", f"{inc['hazard_name']} ({inc['hazard_id']})"),
        ("Severity", str(inc["severity"])),
        ("Camera", inc["camera_name"]),
        ("Area", inc["zone_name"]),
        ("Time", f"{_when(inc['start_time']):%d %b %Y, %H:%M} (Pakistan time)"),
        ("Worker ID", worker),
        ("Confidence", confidence),
    ]


def compose(inc: dict[str, Any], kind: AlertKind, cfg: EmailConfig) -> EmailContent:
    escalation = kind == AlertKind.ESCALATION
    hhmm = f"{_when(inc['start_time']):%H:%M}"
    subject = (f"[RADS] NOT REVIEWED 10 min – {inc['hazard_name']} – {inc['camera_name']}" if escalation
               else f"[RADS] {inc['hazard_name']} – {inc['camera_name']} – {hhmm}")
    lead = ("This incident has not been reviewed for 10 minutes." if escalation
            else "A new food-safety incident was detected.")
    link = f"{cfg.dashboard_url}/incidents/{inc['incident_id']}" if cfg.dashboard_url else None
    lines = incident_lines(inc)

    text = "\n".join([lead, ""] + [f"{k}: {v}" for k, v in lines]
                     + ["", "Please check and review this incident in RADS."]
                     + ([f"Open incident: {link}"] if link else []) + ["", "--", FOOTER])

    e = html.escape
    rows = "".join(f'<tr><td style="padding:6px 12px 6px 0;color:#64748b;white-space:nowrap;vertical-align:top">{e(k)}</td>'
                   f'<td style="padding:6px 0;color:#0f172a;font-weight:600">{e(v)}</td></tr>' for k, v in lines)
    accent = "#b45309" if escalation else "#1f5f8b"
    button = (f'<p style="margin:20px 0"><a href="{e(link, quote=True)}" style="background:#1f5f8b;color:#ffffff;'
              'text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600;display:inline-block">'
              'Open incident</a></p>') if link else ""
    body = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f1f5f9">'
        '<div style="max-width:560px;margin:0 auto;padding:16px;font-family:Segoe UI,Arial,sans-serif;font-size:15px;'
        'line-height:1.5;color:#0f172a">'
        f'<div style="background:#ffffff;border-radius:12px;border-top:6px solid {accent};padding:20px">'
        f'<p style="margin:0 0 4px;font-size:13px;color:{accent};font-weight:700;letter-spacing:.04em">'
        f'{"NOT REVIEWED FOR 10 MINUTES" if escalation else "RADS ALERT"}</p>'
        f'<h1 style="margin:0 0 8px;font-size:20px">{e(inc["hazard_name"])} – {e(inc["camera_name"])}</h1>'
        f'<p style="margin:0 0 12px;color:#334155">{e(lead)}</p>'
        f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
        '<p style="margin:16px 0 0">Please check and review this incident in RADS.</p>'
        f'{button}</div>'
        f'<p style="margin:12px 4px;font-size:12px;color:#64748b">{e(FOOTER)}</p>'
        '</div></body></html>')

    attachment = None
    if cfg.attach_snapshot and inc.get("evidence_path") and cfg.evidence_dir is not None:
        base = cfg.evidence_dir.resolve()
        path = (base / inc["evidence_path"]).resolve()
        if path.is_relative_to(base) and path.is_file():
            attachment = (f"incident-{inc['incident_id']}.jpg", path.read_bytes())
    return EmailContent(subject, text, body, attachment)


def build_message(content: EmailContent, to: str, cfg: EmailConfig) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = content.subject
    msg["From"] = cfg.sender
    msg["To"] = to
    msg.set_content(content.text)
    msg.add_alternative(content.html, subtype="html")
    if content.attachment:
        name, data = content.attachment
        msg.add_attachment(data, maintype="image", subtype="jpeg", filename=name)
    return msg


def test_content(cfg: EmailConfig, by: str) -> EmailContent:
    text = (f"This is a test email from RADS, sent by {by}. If you can read it, email alerts are working.\n\n--\n"
            f"{FOOTER}")
    body = ('<!doctype html><html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#0f172a">'
            f'<p>This is a test email from RADS, sent by {html.escape(by)}.</p>'
            '<p><b>If you can read it, email alerts are working.</b></p>'
            f'<p style="font-size:12px;color:#64748b">{html.escape(FOOTER)}</p></body></html>')
    return EmailContent("RADS test email – alerts are working", text, body)


# ---------------------------------------------------------------- delivery

class EmailError(Exception):
    """A plain-language reason, safe to show and store (never contains the password)."""


def _server_text(e: smtplib.SMTPResponseException) -> str:
    raw = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
    return " ".join(raw.split())[:120]


class EmailSender:
    channel = AlertChannel.EMAIL

    def __init__(self, cfg: EmailConfig, smtp_factory: Callable[..., smtplib.SMTP] | None = None,
                 sleep: Callable[[float], None] | None = None):
        # looked up now (not as default arguments), so tests can replace smtplib.SMTP / time.sleep
        self.cfg, self.smtp_factory, self.sleep = cfg, smtp_factory or smtplib.SMTP, sleep or time.sleep

    def recipients(self, users: list[dict[str, Any]]) -> list:
        from app.alerts import Recipient  # noqa: PLC0415
        return [Recipient(u["user_id"], u["email"].strip()) for u in users
                if u.get("email") and valid_email(u["email"].strip())]

    def compose(self, inc: dict[str, Any], kind: AlertKind) -> EmailContent:
        return compose(inc, kind, self.cfg)

    def _deliver(self, msg: EmailMessage) -> None:
        with self.smtp_factory(self.cfg.host, self.cfg.port, timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.ehlo()
            if self.cfg.starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            self._login(smtp)
            smtp.send_message(msg)

    def _login(self, smtp: smtplib.SMTP) -> None:
        """One AUTH method only. smtplib.login() tries the next method after a refusal, but Gmail closes the
        connection after a 535, so the real reason ("wrong password") would come out as "connection lost"."""
        methods = str(getattr(smtp, "esmtp_features", {}).get("auth", "")).upper().split()
        if "PLAIN" in methods:
            smtp.user, smtp.password = self.cfg.user, self.cfg.password
            smtp.auth("PLAIN", smtp.auth_plain)  # raises SMTPAuthenticationError on 535
        else:
            smtp.login(self.cfg.user, self.cfg.password)

    def send(self, address: str, content: EmailContent) -> None:
        """Deliver to one address. Retries network problems and temporary (4xx) refusals; raises EmailError."""
        msg = build_message(content, address, self.cfg)
        where = f"{self.cfg.host}:{self.cfg.port}"
        reason = ""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                self._deliver(msg)
                return
            except smtplib.SMTPAuthenticationError:
                raise self._safe(f"{self.cfg.provider} rejected the login – check SMTP_USER and the app password "
                                 "(SMTP_PASSWORD)") from None
            except smtplib.SMTPRecipientsRefused as e:
                code = next(iter(e.recipients.values()), (0, b""))[0]
                if 400 <= code < 500:
                    reason = f"The mail server is busy (temporary error {code})"
                else:
                    raise self._safe(f"The mail server refused the address {mask_address(address)} (error {code})") from None
            except smtplib.SMTPSenderRefused as e:
                raise self._safe(f"The mail server refused the sender address SMTP_FROM (error {e.smtp_code})") from None
            except smtplib.SMTPResponseException as e:
                if 400 <= e.smtp_code < 500:
                    reason = f"The mail server is busy (temporary error {e.smtp_code})"
                elif e.smtp_code == 550:
                    raise self._safe(f"The mail server refused the address {mask_address(address)} (error 550)") from None
                else:
                    raise self._safe(f"The mail server refused the email (error {e.smtp_code}: "
                                     f"{_server_text(e)})") from None
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, TimeoutError) as e:
                reason = f"Could not reach the mail server {where} ({'timed out' if 'time' in type(e).__name__.lower() else 'connection lost'})"
            except ssl.SSLError:
                raise self._safe(f"Secure connection (STARTTLS) to {where} failed – check SMTP_PORT and "
                                 "SMTP_STARTTLS") from None
            except OSError as e:
                reason = f"Could not reach the mail server {where} ({e.strerror or type(e).__name__})"
            except smtplib.SMTPException as e:
                reason = f"Email could not be sent ({type(e).__name__})"
            if attempt < ATTEMPTS:
                log.info("email to %s: attempt %d failed (%s), trying again", mask_address(address), attempt, reason)
                self.sleep(RETRY_WAIT_S[attempt - 1])
        raise self._safe(f"{reason} – tried {ATTEMPTS} times")

    def _safe(self, text: str) -> EmailError:
        if self.cfg.password:
            text = text.replace(self.cfg.password, "***")
        return EmailError(text)
