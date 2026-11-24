"""Phase 6b: what the incident detail page needs from GET /api/incidents/{id} – review history for the timeline,
alert recipients, and the rule values in force when the incident started."""
from __future__ import annotations

from datetime import datetime, timedelta

from app_helpers import ctx  # noqa: F401  (fixture)
from shared.contract import TZ, now_iso


def _detail(ctx, iid, user="admin"):
    r = ctx.client.get(f"/api/incidents/{iid}", headers=ctx.headers(user))
    assert r.status_code == 200, r.text
    return r.json()


def _ago(minutes: int) -> str:
    return (datetime.now(TZ) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


# ---------------------------------------------------------------- history (timeline)

def test_new_incident_has_no_history(ctx):
    iid = ctx.add_incident("H-01")
    assert _detail(ctx, iid)["history"] == []


def test_history_lists_every_review_in_order(ctx):
    iid = ctx.add_incident("H-01")
    r1 = ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "Acknowledged"},
                          headers=ctx.headers("manager"))
    assert r1.status_code == 200
    r2 = ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "Confirmed violation", "note": "hairnet off"},
                          headers=ctx.headers("admin"))
    assert r2.status_code == 200
    history = r2.json()["history"]  # the review response is the updated detail, so the page can redraw from it
    assert [(e["user_name"], e["old_status"], e["new_status"], e["note"]) for e in history] == [
        ("Manager", "New", "Acknowledged", None),
        ("Admin", "Acknowledged", "Confirmed violation", "hairnet off"),
    ]
    assert history[0]["user_id"] == ctx.ids["manager"] and history[0]["at"] <= history[1]["at"]
    assert _detail(ctx, iid, "tech")["history"] == history  # every role sees the same timeline


def test_history_ignores_other_incidents(ctx):
    a, b = ctx.add_incident("H-01"), ctx.add_incident("H-01")
    ctx.client.patch(f"/api/incidents/{a}/review", json={"status": "False alarm"}, headers=ctx.headers("manager"))
    assert _detail(ctx, b)["history"] == []


def test_history_falls_back_to_the_incident_row(ctx):
    """A review written without an audit row (e.g. backfilled test data) still shows on the timeline."""
    at = _ago(5)
    iid = ctx.add_incident("H-01", status="False alarm", reviewed_by=ctx.ids["manager"], reviewed_at=at,
                           review_note="shadow")
    assert _detail(ctx, iid)["history"] == [{"at": at, "user_id": ctx.ids["manager"], "user_name": "Manager",
                                             "old_status": None, "new_status": "False alarm", "note": "shadow"}]


# ---------------------------------------------------------------- alerts

def test_alerts_include_recipient_name(ctx):
    iid = ctx.add_incident("H-01")
    t = now_iso()
    ctx.execute("INSERT INTO alerts (incident_id, user_id, channel, kind, sent_at, status, error) VALUES "
                "(:i, NULL, 'dashboard', 'first', :t, 'sent', NULL), "
                "(:i, :m, 'whatsapp', 'first', :t, 'failed', 'HTTP 400: outside 24 h window'), "
                "(:i, NULL, 'telegram', 'escalation', :t, 'sent', NULL)", i=iid, t=t, m=ctx.ids["manager"])
    alerts = _detail(ctx, iid)["alerts"]
    assert [(a["channel"], a["kind"], a["status"], a["recipient_name"]) for a in alerts] == [
        ("dashboard", "first", "sent", None),
        ("whatsapp", "first", "failed", "Manager"),
        ("telegram", "escalation", "sent", None),
    ]
    assert alerts[1]["error"] == "HTTP 400: outside 24 h window"


# ---------------------------------------------------------------- rule values at the time

def test_rule_values_are_those_in_force_when_the_incident_started(ctx):
    rid = ctx.rule("H-01")["rule_id"]
    before = ctx.add_incident("H-01", start_time=_ago(60))
    # Make the audit row clearly later than the incident (both have one-second resolution).
    r = ctx.client.patch(f"/api/rules/{rid}", json={"conf_threshold": 0.6, "window_s": 8, "severity": "High"},
                         headers=ctx.headers("tech"))
    assert r.status_code == 200, r.text
    after = ctx.add_incident("H-01", start_time=(datetime.now(TZ) + timedelta(seconds=5)).isoformat(timespec="seconds"))

    old = _detail(ctx, before)
    assert old["rule_changed_since"] is True
    assert (old["rule"]["conf_threshold"], old["rule"]["window_s"], old["rule"]["severity"]) == (0.45, 5.0, "Medium")
    assert old["rule"]["min_ratio"] == ctx.rule("H-01")["min_ratio"]  # unchanged fields keep their value

    new = _detail(ctx, after)
    assert new["rule_changed_since"] is False
    assert (new["rule"]["conf_threshold"], new["rule"]["window_s"], new["rule"]["severity"]) == (0.6, 8.0, "High")


def test_rule_values_undo_several_changes_newest_first(ctx):
    rid = ctx.rule("H-01")["rule_id"]
    iid = ctx.add_incident("H-01", start_time=_ago(60))
    h = ctx.headers("tech")
    assert ctx.client.patch(f"/api/rules/{rid}", json={"window_s": 8}, headers=h).status_code == 200
    assert ctx.client.patch(f"/api/rules/{rid}", json={"window_s": 12}, headers=h).status_code == 200
    d = _detail(ctx, iid)
    assert d["rule"]["window_s"] == 5.0 and d["rule_changed_since"] is True


def test_rule_unchanged_since(ctx):
    iid = ctx.add_incident("H-04", track_id=None)
    d = _detail(ctx, iid)
    assert d["rule_changed_since"] is False and d["rule"]["min_ratio"] == 0.30
