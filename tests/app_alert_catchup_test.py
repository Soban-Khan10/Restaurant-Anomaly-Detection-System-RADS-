"""Catch-up for missed first alerts: incidents the worker reported while the app was down (message 2 lost)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app import alerts
from app.main import add_jobs
from app_alerts_test import FakeSender, add_user
from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from shared.config import get_settings
from shared.contract import TZ

NOW = datetime(2026, 12, 2, 14, 0, 0, tzinfo=TZ)


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat(timespec="seconds")


def rows(ctx, incident_id):
    return [(r["channel"], r["kind"]) for r in ctx.sql(
        "SELECT channel, kind FROM alerts WHERE incident_id = :i ORDER BY alert_id", i=incident_id)]


@pytest.fixture
def phone(ctx):
    """WhatsApp (faked) to one active Manager with a phone."""
    sender = FakeSender()
    ctx.senders.append(sender)
    add_user(ctx, "m1", "Manager", "+92 300 1111111")
    return sender


def test_recent_missed_incident_gets_its_dashboard_alert_and_texts(ctx, phone):
    iid = ctx.add_incident("H-01", start_time=ago(minutes=5))
    assert alerts.catch_up_first_alerts(ctx.Session, now=NOW) == [iid]
    assert rows(ctx, iid) == [("dashboard", "first"), ("whatsapp", "first")]
    assert len(phone.sent) == 1 and f"Incident #{iid}" in phone.sent[0][1]


def test_old_missed_incident_gets_the_dashboard_alert_only(ctx, phone):
    iid = ctx.add_incident("H-01", start_time=ago(hours=3))
    assert alerts.catch_up_first_alerts(ctx.Session, now=NOW) == [iid]
    assert rows(ctx, iid) == [("dashboard", "first")]
    assert phone.sent == []  # no flood of texts after a long outage


def test_texts_only_within_the_last_60_minutes(ctx, phone):
    edge = ctx.add_incident("H-01", start_time=ago(minutes=60))
    older = ctx.add_incident("H-01", start_time=ago(minutes=61))
    assert alerts.catch_up_first_alerts(ctx.Session, now=NOW) == [edge, older]
    assert rows(ctx, edge) == [("dashboard", "first"), ("whatsapp", "first")]
    assert rows(ctx, older) == [("dashboard", "first")]


def test_safe_to_run_again_and_again(ctx, phone):
    a = ctx.add_incident("H-01", start_time=ago(minutes=5))
    b = ctx.add_incident("H-06", start_time=ago(hours=2))
    assert alerts.catch_up_first_alerts(ctx.Session, now=NOW) == [a, b]
    for later in (NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)):
        assert alerts.catch_up_first_alerts(ctx.Session, now=later) == []
    assert rows(ctx, a) == [("dashboard", "first"), ("whatsapp", "first")]
    assert rows(ctx, b) == [("dashboard", "first")]
    assert len(phone.sent) == 1


def test_leaves_alone_what_does_not_need_it(ctx, phone):
    has_alert = ctx.add_incident("H-01", start_time=ago(minutes=5))
    ctx.execute("INSERT INTO alerts (incident_id, channel, kind, sent_at, status) "
                "VALUES (:i, 'dashboard', 'first', :t, 'sent')", i=has_alert, t=ago(minutes=5))
    reviewed = ctx.add_incident("H-01", start_time=ago(minutes=5), status="False alarm")
    just_now = ctx.add_incident("H-01", start_time=ago(seconds=20))  # the worker's message 2 is still on its way
    assert alerts.catch_up_first_alerts(ctx.Session, now=NOW) == []
    assert rows(ctx, has_alert) == [("dashboard", "first")]
    assert rows(ctx, reviewed) == [] and rows(ctx, just_now) == []
    assert phone.sent == []


def test_a_late_message_2_after_the_catch_up_adds_nothing(ctx, phone):
    iid = ctx.add_incident("H-01", start_time=ago(minutes=5))
    alerts.catch_up_first_alerts(ctx.Session, now=NOW)
    r = ctx.local.post("/internal/incidents/new", json={"incident_id": iid}, headers=INTERNAL)
    assert r.status_code == 200
    assert rows(ctx, iid) == [("dashboard", "first"), ("whatsapp", "first")]
    assert len(phone.sent) == 1


def test_minute_job_catches_up_then_escalates(ctx, phone):
    """App down for 2 hours: the missed incident gets its first alert, then the usual escalation."""
    iid = ctx.add_incident("H-01", start_time=ago(hours=2))
    alerts.run_minute_jobs(ctx.Session, now=NOW)
    assert rows(ctx, iid) == [("dashboard", "first"), ("dashboard", "escalation")]
    assert phone.sent == []  # older than 60 min: no first text; escalation texts Admins (none have a phone here)
    alerts.run_minute_jobs(ctx.Session, now=NOW + timedelta(minutes=1))
    assert rows(ctx, iid) == [("dashboard", "first"), ("dashboard", "escalation")]


def test_runs_at_startup_and_then_every_minute(ctx):
    sched = BackgroundScheduler(timezone="Asia/Karachi")
    add_jobs(sched, ctx.Session, get_settings())
    sched.start(paused=True)
    try:
        job = sched.get_job("escalation")
        assert job.func is alerts.run_minute_jobs
        assert job.trigger.interval == timedelta(minutes=1)
        assert abs(job.next_run_time - datetime.now(TZ)) < timedelta(seconds=10)  # first run right away
    finally:
        sched.shutdown(wait=False)
