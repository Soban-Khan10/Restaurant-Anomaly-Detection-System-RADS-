"""Email alerts: recipients, content, retries, no duplicates, settings, secrets, the Admin test/status routes.

SMTP is always mocked (FakeSMTP): no test connects to a mail server or sends a real email. app_helpers blanks every
SMTP_* setting, so even a .env with real Gmail settings leaves email off unless a test turns it on with fake values.
"""
from __future__ import annotations

import io
import json
import logging
import smtplib
import socket
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app import alerts, email_alerts, log_redaction
from app.email_alerts import FOOTER, NO_RECIPIENTS, EmailConfig, EmailError, EmailSender, build_message, compose
from app.routes import alert_settings
from app_alerts_test import post_new
from app_helpers import PASSWORD_HASH, ctx  # noqa: F401  (ctx is a fixture)
from app_pauses_test import insert as insert_pause
from shared.contract import TZ, AlertKind, now_iso

PASSWORD = "abcd efgh ijkl mnop"   # how Gmail shows an app password
PASSWORD_NOSPACE = PASSWORD.replace(" ", "")
START = "2026-12-02T14:05:00+05:00"
NOW = datetime(2026, 12, 2, 14, 6, 0, tzinfo=TZ)
SMTP_ENV = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": "rads.alerts@gmail.com",
            "SMTP_PASSWORD": PASSWORD, "SMTP_FROM": "RADS Alerts <rads.alerts@gmail.com>"}


def iso(dt):
    return dt.isoformat(timespec="seconds")


class FakeSMTP:
    """Stands in for smtplib.SMTP. `script` holds one entry per connection: None = works, an exception = raised
    at login (where Gmail checks the password) or, for refusals, at send_message."""

    def __init__(self, *script):
        self.script = list(script)
        self.connects: list[tuple] = []
        self.logins: list[tuple[str, str]] = []
        self.starttls = 0
        self.sent: list = []

    def __call__(self, host, port, timeout=None):
        self.connects.append((host, port, timeout))
        outcome = self.script.pop(0) if self.script else None
        return _Conn(self, outcome)

    @property
    def to(self) -> list[str]:
        return [m["To"] for m in self.sent]


class _Conn:
    def __init__(self, fake, outcome):
        self.fake, self.outcome = fake, outcome

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context=None):
        assert context is not None
        self.fake.starttls += 1

    def login(self, user, password):
        self.fake.logins.append((user, password))
        if isinstance(self.outcome, (smtplib.SMTPAuthenticationError, OSError, smtplib.SMTPServerDisconnected)):
            raise self.outcome

    def send_message(self, msg):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        self.fake.sent.append(msg)


def cfg(evidence_dir=None, **kw) -> EmailConfig:
    base = EmailConfig(host="smtp.gmail.com", port=587, user="rads.alerts@gmail.com", password=PASSWORD_NOSPACE,
                       sender="RADS Alerts <rads.alerts@gmail.com>", starttls=True,
                       dashboard_url="http://192.168.1.10:5173", attach_snapshot=False, evidence_dir=evidence_dir)
    return replace(base, **kw)


def sender(ctx, fake=None, **kw):
    waits: list[float] = []
    s = EmailSender(cfg(ctx.evidence_dir, **kw) if ctx else cfg(**kw), fake or FakeSMTP(), waits.append)
    s.waits = waits  # type: ignore[attr-defined]
    return s


def add_user(ctx, username, role, email=None, active=1, full_name=None):
    return ctx.execute("INSERT INTO users (role_id, username, password_hash, full_name, email, active, created_at) "
                       "VALUES ((SELECT role_id FROM roles WHERE name = :r), :u, :h, :f, :e, :a, :t) "
                       "RETURNING user_id", r=role, u=username, h=PASSWORD_HASH, f=full_name or username.title(),
                       e=email, a=active, t=now_iso())


@pytest.fixture
def staff(ctx):
    """Emails: two active Managers (one without email), an inactive Manager, active and inactive Admins, a
    Technical user. The seeded admin/manager/tech have no email."""
    return {
        "m1": add_user(ctx, "m1", "Manager", "m1@example.com", full_name="Ahmad"),
        "m2": add_user(ctx, "m2", "Manager", "m2@example.com"),
        "m_none": add_user(ctx, "m_none", "Manager"),
        "m_off": add_user(ctx, "m_off", "Manager", "off@example.com", active=0),
        "a1": add_user(ctx, "a1", "Admin", "a1@example.com"),
        "a_off": add_user(ctx, "a_off", "Admin", "a_off@example.com", active=0),
        "t1": add_user(ctx, "t1", "Technical", "t1@example.com"),
    }


def email_rows(ctx, incident_id=None):
    q = "SELECT incident_id, user_id, kind, status, error FROM alerts WHERE channel = 'email'"
    if incident_id is not None:
        return ctx.sql(q + " AND incident_id = :i ORDER BY alert_id", i=incident_id)
    return ctx.sql(q + " ORDER BY alert_id")


def summary(ctx, iid):
    with ctx.Session() as db:
        return alerts._incident_summary(db, iid)


def zone_rule(ctx):
    return ctx.sql("SELECT r.hazard_id, r.camera_id FROM rules r JOIN hazards h ON h.hazard_id = r.hazard_id "
                   "WHERE h.tied_to = 'zone' ORDER BY r.rule_id LIMIT 1")[0]


# ---------------------------------------------------------------- settings: on only when all four are set

def test_email_is_off_unless_all_four_settings_are_set(monkeypatch):
    for k, v in SMTP_ENV.items():
        monkeypatch.setenv(k, v)
    on = email_alerts.email_config()
    assert on.enabled and on.port == 587 and on.starttls and on.password == PASSWORD_NOSPACE
    assert [type(s) for s in alerts.configured_senders()] == [EmailSender]  # this test has no ctx: real function
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.setenv(name, "")
        off = email_alerts.email_config()
        assert not off.enabled and off.missing == [name]
        assert alerts.configured_senders() == []  # off, and no error
        monkeypatch.setenv(name, SMTP_ENV[name])
    assert "abcd" not in repr(on) and PASSWORD_NOSPACE not in repr(on)


def test_no_settings_means_dashboard_alert_only(ctx, staff):
    assert not email_alerts.email_config().enabled  # app_helpers blanks SMTP_*; ctx.senders stays empty
    iid = ctx.add_incident()
    assert post_new(ctx, iid).status_code == 200
    assert [r["channel"] for r in ctx.sql("SELECT channel FROM alerts")] == ["dashboard"]


# ---------------------------------------------------------------- recipients

def test_first_alert_goes_to_every_active_manager_with_an_email(ctx, staff):
    fake = FakeSMTP()
    iid = ctx.add_incident()
    assert alerts.send_first_alerts(ctx.Session, iid, [sender(ctx, fake)]) == 2
    assert fake.to == ["m1@example.com", "m2@example.com"]
    assert email_rows(ctx, iid) == [
        {"incident_id": iid, "user_id": staff["m1"], "kind": "first", "status": "sent", "error": None},
        {"incident_id": iid, "user_id": staff["m2"], "kind": "first", "status": "sent", "error": None}]
    assert fake.connects[0] == ("smtp.gmail.com", 587, 15) and fake.starttls == 2
    assert fake.logins[0] == ("rads.alerts@gmail.com", PASSWORD_NOSPACE)


def test_escalation_goes_to_every_active_admin_with_an_email(ctx, staff):
    fake = FakeSMTP()
    iid = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=10)))
    assert alerts.run_escalation(ctx.Session, [sender(ctx, fake)], now=NOW) == [iid]
    assert fake.to == ["a1@example.com"]
    assert [(r["user_id"], r["kind"], r["status"]) for r in email_rows(ctx, iid)] == [
        (staff["a1"], "escalation", "sent")]


def test_nobody_with_an_email_gives_one_failed_row(ctx):
    add_user(ctx, "m1", "Manager")                       # no email
    add_user(ctx, "m2", "Manager", "not-an-address")     # not valid (written past the API)
    fake = FakeSMTP()
    iid = ctx.add_incident()
    for _ in range(3):  # also when it is tried again (catch-up, restart)
        alerts.send_first_alerts(ctx.Session, iid, [sender(ctx, fake)])
    assert fake.connects == []
    assert email_rows(ctx, iid) == [{"incident_id": iid, "user_id": None, "kind": "first", "status": "failed",
                                     "error": NO_RECIPIENTS}]


# ---------------------------------------------------------------- content

def test_worker_hazard_subject_and_body(ctx):
    iid = ctx.add_incident("H-08", start_time=START, track_id=7, conf_avg=0.81, conf_max=0.934)
    inc = summary(ctx, iid)
    c = compose(inc, AlertKind.FIRST, cfg())
    assert c.subject == "[RADS] Mask missing – Camera 1 – 14:05"
    for line in ("Hazard: Mask missing (H-08)", f"Severity: {inc['severity']}", "Camera: Camera 1",
                 f"Area: {inc['zone_name']}", "Time: 02 Dec 2026, 14:05 (Pakistan time)", "Worker ID: #7",
                 "Confidence: average 81%, highest 93%", "Please check and review this incident in RADS.",
                 f"Open incident: http://192.168.1.10:5173/incidents/{iid}", FOOTER):
        assert line in c.text, line
    assert f'href="http://192.168.1.10:5173/incidents/{iid}"' in c.html and ">Open incident</a>" in c.html
    assert "Mask missing (H-08)" in c.html and "#7" in c.html and FOOTER in c.html
    assert "max-width:560px" in c.html  # one narrow column: readable on a phone

    esc = compose(inc, AlertKind.ESCALATION, cfg())
    assert esc.subject == "[RADS] NOT REVIEWED 10 min – Mask missing – Camera 1"
    assert "has not been reviewed for 10 minutes" in esc.text


def test_area_hazard_has_no_worker_id_and_no_button_without_dashboard_url(ctx):
    rule = zone_rule(ctx)
    iid = ctx.add_incident(rule["hazard_id"], camera_id=rule["camera_id"], start_time=START, track_id=None)
    c = compose(summary(ctx, iid), AlertKind.FIRST, cfg(dashboard_url=""))
    assert "Worker ID: —" in c.text
    assert "Open incident" not in c.text and "Open incident" not in c.html and "href" not in c.html


def test_message_is_text_plus_html_alternative(ctx):
    c = compose(summary(ctx, ctx.add_incident(start_time=START)), AlertKind.FIRST, cfg())
    msg = build_message(c, "m1@example.com", cfg())
    assert msg["Subject"] == c.subject and msg["From"] == "RADS Alerts <rads.alerts@gmail.com>"
    assert msg["To"] == "m1@example.com"
    assert [p.get_content_type() for p in msg.walk()] == ["multipart/alternative", "text/plain", "text/html"]
    assert list(msg.iter_attachments()) == []


def test_every_value_is_escaped_in_the_html(ctx):
    iid = ctx.add_incident("H-08", start_time=START)
    inc = {**summary(ctx, iid), "hazard_name": '<script>alert("x")</script>', "zone_name": "Prep & <b>Wash</b>",
           "camera_name": "Cam \"1\" <img src=x onerror=alert(1)>"}
    c = compose(inc, AlertKind.FIRST, cfg(dashboard_url='http://x/"><script>'))
    assert "<script" not in c.html and "<b>" not in c.html and "<img" not in c.html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in c.html and "Prep &amp; &lt;b&gt;Wash" in c.html
    assert 'href="http://x/&quot;&gt;&lt;script&gt;' in c.html
    assert '<script>alert("x")</script>' in c.text  # the plain-text part is not HTML: shown as written


def test_snapshot_attached_only_when_turned_on(ctx):
    iid = ctx.add_incident(start_time=START)
    ctx.add_evidence(iid, content=b"\xff\xd8\xff\xe0real-jpeg\xff\xd9")
    inc = summary(ctx, iid)
    assert compose(inc, AlertKind.FIRST, cfg(ctx.evidence_dir)).attachment is None   # default: off
    on = compose(inc, AlertKind.FIRST, cfg(ctx.evidence_dir, attach_snapshot=True))
    assert on.attachment == (f"incident-{iid}.jpg", b"\xff\xd8\xff\xe0real-jpeg\xff\xd9")
    [part] = build_message(on, "a@example.com", cfg()).iter_attachments()
    assert part.get_content_type() == "image/jpeg" and part.get_filename() == f"incident-{iid}.jpg"
    # a path outside the evidence folder, or a missing file, is never attached
    for bad in ("../../secret.jpg", "2026-12-02/cam1/none.jpg"):
        assert compose({**inc, "evidence_path": bad}, AlertKind.FIRST,
                       cfg(ctx.evidence_dir, attach_snapshot=True)).attachment is None


# ---------------------------------------------------------------- delivery: retries and plain reasons

CONTENT = email_alerts.test_content(cfg(), "Admin")


def test_network_errors_are_retried_then_sent():
    fake = FakeSMTP(smtplib.SMTPServerDisconnected("lost"), socket.timeout("timed out"), None)
    s = sender(None, fake)
    s.send("a@example.com", CONTENT)
    assert len(fake.connects) == 3 and fake.to == ["a@example.com"] and s.waits == [2, 5]


@pytest.mark.parametrize("error, reason", [
    (socket.timeout("timed out"), "Could not reach the mail server smtp.gmail.com:587 (timed out) – tried 3 times"),
    (ConnectionRefusedError(10061, "No connection could be made"),
     "Could not reach the mail server smtp.gmail.com:587 (No connection could be made) – tried 3 times"),
    (smtplib.SMTPDataError(421, b"4.7.0 Try again later"),
     "The mail server is busy (temporary error 421) – tried 3 times"),
    (smtplib.SMTPRecipientsRefused({"a@example.com": (450, b"mailbox busy")}),
     "The mail server is busy (temporary error 450) – tried 3 times"),
])
def test_temporary_errors_are_tried_three_times(error, reason):
    fake = FakeSMTP(error, error, error)
    s = sender(None, fake)
    with pytest.raises(EmailError) as e:
        s.send("a@example.com", CONTENT)
    assert str(e.value) == reason
    assert len(fake.connects) == 3 and s.waits == [2, 5]


@pytest.mark.parametrize("error, reason", [
    (smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"),
     "Gmail rejected the login – check SMTP_USER and the app password (SMTP_PASSWORD)"),
    (smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"5.1.1 no such user")}),
     "The mail server refused the address a@example.com (error 550)"),
    (smtplib.SMTPDataError(550, b"5.7.1 rejected"), "The mail server refused the address a@example.com (error 550)"),
    (smtplib.SMTPDataError(554, b"5.7.0 message looks like spam"),
     "The mail server refused the email (error 554: 5.7.0 message looks like spam)"),
])
def test_permanent_errors_are_not_retried(error, reason):
    fake = FakeSMTP(error, None, None)
    s = sender(None, fake)
    with pytest.raises(EmailError) as e:
        s.send("a@example.com", CONTENT)
    assert str(e.value) == reason
    assert len(fake.connects) == 1 and s.waits == [] and fake.sent == []


class GmailLikeSMTP(smtplib.SMTP):
    """Real smtplib.SMTP logic over no network, answering like Gmail does to a wrong app password: 535 for the first
    AUTH method, then the connection is gone (so a second method fails with "connection lost")."""

    def __init__(self, host, port, timeout=None):
        super().__init__()  # no host: does not connect
        self.auths: list[str] = []
        self.sock = object()  # "connected"

    def ehlo(self, name=""):
        self.ehlo_resp, self.does_esmtp = b"ok", True
        self.esmtp_features = {"auth": "LOGIN PLAIN XOAUTH2 PLAIN-CLIENTTOKEN OAUTHBEARER XOAUTH", "starttls": ""}
        return 250, b"ok"

    def starttls(self, context=None):
        return 220, b"ready"

    def docmd(self, cmd, args=""):
        if cmd == "AUTH":
            self.auths.append(args.split()[0])
            if len(self.auths) > 1:
                raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
            return 535, b"5.7.8 Username and Password not accepted. For more information, go to ..."
        if cmd == "QUIT":
            raise smtplib.SMTPServerDisconnected("closed")
        return 250, b"ok"

    def close(self):
        self.sock = None


def test_gmail_wrong_password_reports_the_login_not_a_lost_connection():
    made: list[GmailLikeSMTP] = []

    def factory(*a, **kw):
        made.append(GmailLikeSMTP(*a, **kw))
        return made[-1]

    s = EmailSender(cfg(), factory, lambda _: None)
    with pytest.raises(EmailError) as e:
        s.send("a@example.com", CONTENT)
    assert str(e.value) == "Gmail rejected the login – check SMTP_USER and the app password (SMTP_PASSWORD)"
    assert len(made) == 1 and made[0].auths == ["PLAIN"]  # one method, one attempt: no retry on 535
    # smtplib's own login() would have hidden the 535 behind the closed connection:
    with pytest.raises(smtplib.SMTPServerDisconnected):
        g = GmailLikeSMTP("h", 587)
        g.ehlo()
        g.login("u", "p")


def test_wrong_app_password_is_recorded_in_plain_words_and_dashboard_alert_stays(ctx, staff):
    bad = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    ctx.senders.append(sender(ctx, FakeSMTP(bad, bad)))
    iid = ctx.add_incident()
    assert post_new(ctx, iid).status_code == 200  # message 2: the worker is never held up or failed
    rows = ctx.sql("SELECT channel, user_id, status, error FROM alerts WHERE incident_id = :i ORDER BY alert_id",
                   i=iid)
    assert rows[0] == {"channel": "dashboard", "user_id": None, "status": "sent", "error": None}
    assert [(r["channel"], r["user_id"], r["status"]) for r in rows[1:]] == [
        ("email", staff["m1"], "failed"), ("email", staff["m2"], "failed")]
    assert rows[1]["error"] == "Gmail rejected the login – check SMTP_USER and the app password (SMTP_PASSWORD)"


# ---------------------------------------------------------------- never twice

def test_no_duplicates_across_message_2_catch_up_and_restart(ctx, staff):
    fake = FakeSMTP()
    ctx.senders.append(sender(ctx, fake))
    iid = ctx.add_incident(start_time=iso(datetime.now(TZ) - timedelta(minutes=2)))
    post_new(ctx, iid)
    post_new(ctx, iid)                                                    # the worker retried message 2
    alerts.run_minute_jobs(ctx.Session)                                   # the catch-up runs every minute
    ctx.senders[:] = [sender(ctx, fake)]                                  # "restart": a new sender, same DB
    alerts.run_minute_jobs(ctx.Session)
    alerts.send_first_alerts(ctx.Session, iid)                            # even sent again directly
    assert fake.to == ["m1@example.com", "m2@example.com"]
    assert [(r["user_id"], r["kind"]) for r in email_rows(ctx, iid)] == [(staff["m1"], "first"),
                                                                         (staff["m2"], "first")]


def test_catch_up_after_the_app_was_down_then_late_message_2(ctx, staff):
    fake = FakeSMTP()
    ctx.senders.append(sender(ctx, fake))
    recent = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=5)))   # message 2 never arrived
    old = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=61)))     # outside the 60-minute rule
    assert alerts.catch_up_first_alerts(ctx.Session, now=NOW) == [recent, old]
    assert fake.to == ["m1@example.com", "m2@example.com"] and email_rows(ctx, old) == []
    post_new(ctx, recent)                                                     # message 2 arrives late
    alerts.catch_up_first_alerts(ctx.Session, now=NOW + timedelta(minutes=1))
    assert len(fake.sent) == 2


def test_escalation_email_once(ctx, staff):
    fake = FakeSMTP()
    s = sender(ctx, fake)
    iid = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=11)))
    for minute in range(4):
        alerts.run_minute_jobs(ctx.Session, [s], now=NOW + timedelta(minutes=minute))
    assert [m["To"] for m in fake.sent if "NOT REVIEWED" in m["Subject"]] == ["a1@example.com"]
    alerts.send_external(ctx.Session, iid, AlertKind.ESCALATION, (alerts.RoleName.ADMIN,), [s])  # restart path
    assert len([r for r in email_rows(ctx, iid) if r["kind"] == "escalation"]) == 1


def test_escalation_messages_only_for_incidents_from_the_last_60_minutes(ctx, staff):
    """E.g. the computer was asleep: old incidents still New get the dashboard escalation, but no email/WhatsApp."""
    from app_alerts_test import FakeSender
    ctx.execute("UPDATE users SET phone = '+923009999999' WHERE user_id = :u", u=staff["a1"])
    fake, whatsapp = FakeSMTP(), FakeSender()
    senders = [sender(ctx, fake), whatsapp]
    edge = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=60)))               # exactly 60 min: still sent
    recent = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=25)))
    old = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=60, seconds=1)))
    very_old = ctx.add_incident(start_time=iso(NOW - timedelta(hours=9)))
    assert alerts.run_escalation(ctx.Session, senders, now=NOW) == [edge, recent, old, very_old]

    assert [m["To"] for m in fake.sent] == ["a1@example.com", "a1@example.com"]
    assert [a for a, _ in whatsapp.sent] == ["+923009999999", "+923009999999"]
    for iid in (edge, recent):
        assert sorted(r["channel"] for r in ctx.sql("SELECT channel FROM alerts WHERE incident_id = :i AND "
                                                    "kind = 'escalation'", i=iid)) == ["dashboard", "email", "whatsapp"]
    for iid in (old, very_old):  # dashboard row only, and marked escalated so it is never picked again
        assert [r["channel"] for r in ctx.sql("SELECT channel FROM alerts WHERE incident_id = :i AND "
                                              "kind = 'escalation'", i=iid)] == ["dashboard"]
        assert ctx.sql("SELECT escalated_at FROM incidents WHERE incident_id = :i", i=iid)[0]["escalated_at"] == iso(NOW)
    alerts.run_minute_jobs(ctx.Session, senders, now=NOW + timedelta(minutes=1))  # (the catch-up sends first alerts)
    assert len([m for m in fake.sent if "NOT REVIEWED" in m["Subject"]]) == 2
    assert len([t for _, t in whatsapp.sent if "ESCALATION" in t]) == 2


def test_a_failed_email_is_not_counted_as_sent_but_not_retried_by_the_catch_up(ctx, staff):
    bad = smtplib.SMTPAuthenticationError(535, b"no")
    fake = FakeSMTP(bad, bad)
    ctx.senders.append(sender(ctx, fake))
    iid = ctx.add_incident(start_time=iso(datetime.now(TZ) - timedelta(minutes=2)))
    post_new(ctx, iid)
    alerts.run_minute_jobs(ctx.Session)
    assert len(fake.connects) == 2 and [r["status"] for r in email_rows(ctx, iid)] == ["failed", "failed"]


def test_no_email_for_an_incident_during_a_pause(ctx, staff):
    fake = FakeSMTP()
    ctx.senders.append(sender(ctx, fake))
    insert_pause(ctx, camera_id=1)                         # active since 10 minutes ago
    iid = ctx.add_incident(start_time=iso(datetime.now(TZ) - timedelta(minutes=1)))
    assert post_new(ctx, iid).status_code == 200
    assert alerts.send_first_alerts(ctx.Session, iid) == 0  # also refused when called directly
    old = ctx.add_incident(start_time=iso(datetime.now(TZ) - timedelta(minutes=5)))
    alerts.run_minute_jobs(ctx.Session, now=datetime.now(TZ) + timedelta(minutes=6))  # catch-up + escalation
    assert fake.sent == [] and email_rows(ctx) == []
    assert ctx.sql("SELECT COUNT(*) AS n FROM alerts WHERE incident_id = :i AND channel = 'dashboard'",
                   i=old)[0]["n"] >= 1


# ---------------------------------------------------------------- the password never leaks

def test_password_never_in_logs_alerts_or_audit(ctx, staff, monkeypatch):
    for k, v in SMTP_ENV.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    handler.addFilter(log_redaction.FILTER)  # what install() does for every real handler
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        # a careless server echoing the password back, and code that prints it in every way
        echo = smtplib.SMTPDataError(554, f"rejected for {PASSWORD_NOSPACE}".encode())
        ctx.senders.append(sender(ctx, FakeSMTP(echo, echo)))
        iid = ctx.add_incident()
        post_new(ctx, iid)
        log = logging.getLogger("rads.test")
        log.warning("password is %s", PASSWORD)
        log.warning("password is " + PASSWORD_NOSPACE)
        log.warning("config %r", email_alerts.email_config())
        try:
            raise RuntimeError(f"login failed with {PASSWORD}")
        except RuntimeError:
            log.exception("boom")
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    text = out.getvalue()
    assert "password is ***" in text and "***" in text
    for secret in (PASSWORD, PASSWORD_NOSPACE):
        assert secret not in text
        dump = json.dumps(ctx.sql("SELECT * FROM alerts") + ctx.sql("SELECT * FROM audit_log"))
        assert secret not in dump
    errors = [r["error"] for r in email_rows(ctx)]
    assert errors and all("***" in e for e in errors)
    status = ctx.client.get("/api/alerts/status", headers=ctx.headers("admin")).text
    assert PASSWORD not in status and PASSWORD_NOSPACE not in status and "password" not in status.lower()


# ---------------------------------------------------------------- Admin: test email and status

@pytest.fixture
def smtp_on(monkeypatch):
    """Email on with fake settings; smtplib.SMTP replaced, so nothing leaves the machine."""
    for k, v in SMTP_ENV.items():
        monkeypatch.setenv(k, v)
    fake = FakeSMTP()
    monkeypatch.setattr(smtplib, "SMTP", fake)
    monkeypatch.setattr(email_alerts.time, "sleep", lambda s: None)
    monkeypatch.setattr(alert_settings, "last_test", None)
    return fake


def test_test_email_is_admin_only(ctx, smtp_on):
    assert ctx.client.post("/api/alerts/test-email", json={"to": "x@example.com"}).status_code == 401
    assert ctx.client.get("/api/alerts/status").status_code == 401
    for user in ("manager", "tech"):
        h = ctx.headers(user)
        assert ctx.client.post("/api/alerts/test-email", json={"to": "x@example.com"}, headers=h).status_code == 403
        assert ctx.client.get("/api/alerts/status", headers=h).status_code == 403
    assert smtp_on.connects == [] and ctx.audit("test_email") == []


def test_test_email_to_the_admin_or_a_given_address(ctx, smtp_on):
    h = ctx.headers("admin")
    r = ctx.client.post("/api/alerts/test-email", headers=h)
    assert r.status_code == 422 and "Users page" in r.json()["detail"]  # the admin has no email yet
    ctx.execute("UPDATE users SET email = 'boss@example.com' WHERE user_id = :u", u=ctx.ids["admin"])
    r = ctx.client.post("/api/alerts/test-email", headers=h)
    assert r.json() == {"status": "sent", "to": "boss@example.com", "reason": None}
    r = ctx.client.post("/api/alerts/test-email", json={"to": "other@example.com"}, headers=h)
    assert r.json()["status"] == "sent"
    assert smtp_on.to == ["boss@example.com", "other@example.com"]
    assert smtp_on.sent[0]["Subject"] == "RADS test email – alerts are working"
    assert ctx.client.post("/api/alerts/test-email", json={"to": "nope"}, headers=h).status_code == 422
    audit = ctx.audit("test_email")
    assert [a["details"] for a in audit] == [{"to": "boss@example.com", "status": "sent", "reason": None},
                                             {"to": "other@example.com", "status": "sent", "reason": None}]
    assert audit[0]["user_id"] == ctx.ids["admin"]


def test_test_email_failure_returns_the_plain_reason(ctx, smtp_on):
    smtp_on.script = [smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")]
    r = ctx.client.post("/api/alerts/test-email", json={"to": "a@example.com"}, headers=ctx.headers("admin"))
    assert r.status_code == 200
    assert r.json() == {"status": "failed", "to": "a@example.com",
                        "reason": "Gmail rejected the login – check SMTP_USER and the app password (SMTP_PASSWORD)"}
    assert ctx.audit("test_email")[0]["details"]["status"] == "failed"


def test_test_email_when_email_is_off(ctx, smtp_on, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "")
    r = ctx.client.post("/api/alerts/test-email", json={"to": "a@example.com"}, headers=ctx.headers("admin"))
    assert r.json()["status"] == "failed" and "SMTP_PASSWORD" in r.json()["reason"]
    assert smtp_on.connects == []


def test_status(ctx, staff, smtp_on):
    h = ctx.headers("admin")
    s = ctx.client.get("/api/alerts/status", headers=h).json()
    e = s["email"]
    assert e["enabled"] and e["missing"] == [] and e["host"] == "smtp.gmail.com" and e["port"] == 587
    assert e["user"] == "rads.alerts@gmail.com" and e["sender"] == "RADS Alerts <rads.alerts@gmail.com>"
    assert e["managers_with_email"] == 2 and e["admins_with_email"] == 1  # active ones only
    assert e["last_alert"] is None and e["last_test"] is None
    assert s["whatsapp"] is False and s["telegram"] is False

    ctx.senders.append(sender(ctx, FakeSMTP()))
    iid = ctx.add_incident()
    post_new(ctx, iid)
    ctx.client.post("/api/alerts/test-email", json={"to": "a@example.com"}, headers=h)
    e = ctx.client.get("/api/alerts/status", headers=h).json()["email"]
    assert e["last_alert"]["incident_id"] == iid and e["last_alert"]["status"] == "sent"
    assert e["last_alert"]["recipient"] == "M2"
    assert e["last_test"]["status"] == "sent" and e["last_test"]["by"] == "Admin"


def test_status_when_off_lists_what_is_missing(ctx):
    e = ctx.client.get("/api/alerts/status", headers=ctx.headers("admin")).json()["email"]
    assert not e["enabled"] and e["missing"] == ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]


def test_incident_detail_lists_email_rows_with_the_recipient(ctx, staff):
    ctx.senders.append(sender(ctx, FakeSMTP()))
    iid = ctx.add_incident()
    post_new(ctx, iid)
    d = ctx.client.get(f"/api/incidents/{iid}", headers=ctx.headers("manager")).json()
    emails = [a for a in d["alerts"] if a["channel"] == "email"]
    assert [(a["status"], a["user_id"]) for a in emails] == [("sent", staff["m1"]), ("sent", staff["m2"])]


# ---------------------------------------------------------------- users: the email field

def test_users_email_field_validated_and_audited(ctx):
    h = ctx.headers("admin")
    base = {"username": "sara", "password": "long-enough-pass", "role": "Manager", "full_name": "Sara"}
    assert ctx.client.post("/api/users", json={**base, "email": "not an email"}, headers=h).status_code == 422
    r = ctx.client.post("/api/users", json={**base, "email": " sara@example.com "}, headers=h)
    assert r.status_code in (200, 201) and r.json()["email"] == "sara@example.com"
    uid = r.json()["user_id"]
    assert ctx.client.patch(f"/api/users/{uid}", json={"email": "bad@"}, headers=h).status_code == 422
    r = ctx.client.patch(f"/api/users/{uid}", json={"email": "sara.k@example.com"}, headers=h)
    assert r.json()["email"] == "sara.k@example.com"
    r = ctx.client.patch(f"/api/users/{uid}", json={"email": ""}, headers=h)
    assert r.json()["email"] is None                             # blank removes it
    changes = [a["details"] for a in ctx.audit("user_update")]
    assert changes[0]["email"] == {"old": "sara@example.com", "new": "sara.k@example.com"}
    assert changes[1]["email"] == {"old": "sara.k@example.com", "new": None}
    assert ctx.audit("user_create")[0]["details"]["email"] == "sara@example.com"
