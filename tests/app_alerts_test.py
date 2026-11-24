"""Phase 3: alerts (dashboard rows, WhatsApp/Telegram with senders mocked, failures recorded) and escalation."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest

from app import alerts
from app_helpers import INTERNAL, PASSWORD_HASH, ctx  # noqa: F401  (ctx is a fixture)
from shared.config import get_settings
from shared.contract import TZ, AlertChannel, now_iso

NOW = datetime(2026, 12, 2, 14, 0, 0, tzinfo=TZ)


def iso(dt):
    return dt.isoformat(timespec="seconds")


class FakeSender:
    """Records messages instead of calling an API. `fail_for` addresses raise like a failed API call."""

    def __init__(self, channel=AlertChannel.WHATSAPP, fail_for=(), per_user=True):
        self.channel, self.fail_for, self.per_user = channel, set(fail_for), per_user
        self.sent: list[tuple[str, str]] = []

    def recipients(self, users):
        if not self.per_user:
            return [alerts.Recipient(None, "-100123")]
        return [alerts.Recipient(u["user_id"], u["phone"]) for u in users if u["phone"]]

    def send(self, address, message):
        if address in self.fail_for:
            raise alerts.SendError("HTTP 400: recipient not allowed")
        self.sent.append((address, message))


def add_user(ctx, username, role, phone=None, active=1):
    return ctx.execute("INSERT INTO users (role_id, username, password_hash, phone, active, created_at) "
                       "VALUES ((SELECT role_id FROM roles WHERE name = :r), :u, :h, :p, :a, :t) RETURNING user_id",
                       r=role, u=username, h=PASSWORD_HASH, p=phone, a=active, t=now_iso())


@pytest.fixture
def staff(ctx):
    """Phones: two active Managers, one inactive Manager, one Admin. The seeded users have none."""
    return {
        "m1": add_user(ctx, "m1", "Manager", "+92 300 1111111"),
        "m2": add_user(ctx, "m2", "Manager", "+923002222222"),
        "m_off": add_user(ctx, "m_off", "Manager", "+923003333333", active=0),
        "a1": add_user(ctx, "a1", "Admin", "+923009999999"),
    }


def alert_rows(ctx, incident_id=None):
    q = "SELECT incident_id, user_id, channel, kind, status, error FROM alerts"
    if incident_id is not None:
        return ctx.sql(q + " WHERE incident_id = :i ORDER BY alert_id", i=incident_id)
    return ctx.sql(q + " ORDER BY alert_id")


def post_new(ctx, iid):
    return ctx.local.post("/internal/incidents/new", json={"incident_id": iid}, headers=INTERNAL)


# ---------------------------------------------------------------- first alert

def test_new_incident_writes_one_dashboard_alert(ctx):
    iid = ctx.add_incident()
    assert post_new(ctx, iid).status_code == 200
    assert alert_rows(ctx) == [{"incident_id": iid, "user_id": None, "channel": "dashboard", "kind": "first",
                                "status": "sent", "error": None}]


def test_retried_message_does_not_duplicate_alerts(ctx, staff):
    sender = FakeSender()
    ctx.senders.append(sender)
    iid = ctx.add_incident()
    post_new(ctx, iid)
    post_new(ctx, iid)  # worker retry
    assert len([r for r in alert_rows(ctx) if r["channel"] == "dashboard"]) == 1
    assert len(sender.sent) == 2  # m1, m2 once each


def test_whatsapp_to_every_active_manager_with_phone(ctx, staff):
    sender = FakeSender()
    ctx.senders.append(sender)
    iid = ctx.add_incident("H-05", 1, iso(NOW), track_id=None)
    assert post_new(ctx, iid).status_code == 200

    assert [a for a, _ in sender.sent] == ["+92 300 1111111", "+923002222222"]  # not inactive, not Admin
    msg = sender.sent[0][1]
    assert "H-05 Bin left open" in msg and "Camera 1" in msg and "Waste bin" in msg and "14:00" in msg
    assert f"#{iid}" in msg
    rows = [r for r in alert_rows(ctx, iid) if r["channel"] == "whatsapp"]
    assert [(r["user_id"], r["kind"], r["status"]) for r in rows] == [(staff["m1"], "first", "sent"),
                                                                       (staff["m2"], "first", "sent")]


def test_failed_send_is_recorded_and_breaks_nothing(ctx, staff):
    wa = FakeSender(fail_for={"+923002222222"})
    tg = FakeSender(AlertChannel.TELEGRAM, per_user=False)
    ctx.senders.extend([wa, tg])
    iid = ctx.add_incident()

    assert post_new(ctx, iid).status_code == 200
    rows = alert_rows(ctx, iid)
    by = {(r["channel"], r["user_id"]): r for r in rows}
    assert by[("whatsapp", staff["m1"])]["status"] == "sent"
    failed = by[("whatsapp", staff["m2"])]
    assert failed["status"] == "failed" and "recipient not allowed" in failed["error"]
    assert by[("telegram", None)]["status"] == "sent"  # the other channel still went out
    assert by[("dashboard", None)]["status"] == "sent"


def test_sender_crash_never_raises(ctx, staff):
    class Exploding(FakeSender):
        def recipients(self, users):
            raise RuntimeError("bug in sender")

    ctx.senders.append(Exploding())
    iid = ctx.add_incident()
    assert post_new(ctx, iid).status_code == 200
    assert [r["channel"] for r in alert_rows(ctx)] == ["dashboard"]


def test_no_channels_configured_means_dashboard_only(ctx, staff):
    iid = ctx.add_incident()
    post_new(ctx, iid)
    assert [r["channel"] for r in alert_rows(ctx)] == ["dashboard"]


# ---------------------------------------------------------------- real sender classes (HTTP mocked)

def test_whatsapp_sender_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid"}]})

    s = alerts.WhatsAppSender("WA_SECRET", "1234", httpx.Client(transport=httpx.MockTransport(handler)))
    [rcpt] = s.recipients([{"user_id": 5, "phone": "+92 300-1234567"}, {"user_id": 6, "phone": None}])
    assert rcpt == alerts.Recipient(5, "923001234567")
    s.send(rcpt.address, "hello")
    req = seen[0]
    assert str(req.url) == "https://graph.facebook.com/v21.0/1234/messages"
    assert req.headers["authorization"] == "Bearer WA_SECRET"
    assert json.loads(req.content) == {"messaging_product": "whatsapp", "to": "923001234567", "type": "text",
                                       "text": {"preview_url": False, "body": "hello"}}


def test_telegram_sender_request_and_secret_never_in_error():
    seen = []

    def ok(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    s = alerts.TelegramSender("123:BOTSECRET", ["-100555"], httpx.Client(transport=httpx.MockTransport(ok)))
    assert s.recipients([{"user_id": 1, "phone": "+92300"}]) == [alerts.Recipient(None, "-100555")]
    s.send("-100555", "hi")
    assert str(seen[0].url) == "https://api.telegram.org/bot123:BOTSECRET/sendMessage"
    assert json.loads(seen[0].content) == {"chat_id": "-100555", "text": "hi"}

    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    s.client = httpx.Client(transport=httpx.MockTransport(down))
    with pytest.raises(alerts.SendError) as e:
        s.send("-100555", "hi")
    assert "BOTSECRET" not in str(e.value) and "***" in str(e.value)

    s.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="bot was blocked")))
    with pytest.raises(alerts.SendError, match="HTTP 403: bot was blocked"):
        s.send("-100555", "hi")


def test_configured_senders():
    base = replace(get_settings(), whatsapp_token="", whatsapp_phone_id="", telegram_bot_token="")
    real = alerts.configured_senders  # this test doesn't use ctx, so it is not monkeypatched
    assert real(base, "") == []
    wa = real(replace(base, whatsapp_token="t", whatsapp_phone_id="p"), "")
    assert [s.channel for s in wa] == [AlertChannel.WHATSAPP]
    assert real(replace(base, telegram_bot_token="b"), "") == []  # no chat id -> off
    tg = real(replace(base, telegram_bot_token="b"), "-1, -2")
    assert [s.channel for s in tg] == [AlertChannel.TELEGRAM] and tg[0].chat_ids == ["-1", "-2"]


# ---------------------------------------------------------------- escalation

def test_escalation_after_10_minutes_to_admins(ctx, staff):
    sender = FakeSender()
    old = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=10)))
    young = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=9, seconds=59)))
    reviewed = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=30)), status="Acknowledged")
    done = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=30)), escalated_at=iso(NOW - timedelta(minutes=5)))

    assert alerts.run_escalation(ctx.Session, [sender], now=NOW) == [old]

    inc = {r["incident_id"]: r for r in ctx.sql("SELECT incident_id, escalated_at FROM incidents")}
    assert inc[old]["escalated_at"] == iso(NOW)
    assert inc[young]["escalated_at"] is None and inc[reviewed]["escalated_at"] is None
    assert inc[done]["escalated_at"] == iso(NOW - timedelta(minutes=5))

    assert [a for a, _ in sender.sent] == ["+923009999999"]  # Admin only
    assert "ESCALATION" in sender.sent[0][1]
    assert [(r["channel"], r["kind"], r["user_id"], r["status"]) for r in alert_rows(ctx, old)] == [
        ("dashboard", "escalation", None, "sent"), ("whatsapp", "escalation", staff["a1"], "sent")]

    # Runs every minute: nothing is escalated twice.
    assert alerts.run_escalation(ctx.Session, [sender], now=NOW + timedelta(minutes=1)) == [young]
    assert alerts.run_escalation(ctx.Session, [sender], now=NOW + timedelta(minutes=2)) == []
    assert len(alert_rows(ctx, old)) == 2


def test_escalation_failure_recorded(ctx, staff):
    iid = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=11)))
    assert alerts.run_escalation(ctx.Session, [FakeSender(fail_for={"+923009999999"})], now=NOW) == [iid]
    failed = [r for r in alert_rows(ctx, iid) if r["channel"] == "whatsapp"]
    assert failed[0]["status"] == "failed" and failed[0]["error"]


def test_escalation_with_no_channels_still_marks_and_writes_dashboard_row(ctx):
    iid = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=11)))
    assert alerts.run_escalation(ctx.Session, [], now=NOW) == [iid]
    assert [(r["channel"], r["kind"]) for r in alert_rows(ctx, iid)] == [("dashboard", "escalation")]


def test_review_before_escalation_prevents_it(ctx):
    iid = ctx.add_incident(start_time=iso(NOW - timedelta(minutes=11)))
    ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "Acknowledged"}, headers=ctx.headers("manager"))
    assert alerts.run_escalation(ctx.Session, [], now=NOW) == []
