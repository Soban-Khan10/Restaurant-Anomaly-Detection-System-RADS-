"""Pause monitoring (app side): routes, validation, status, WebSocket events, the transition job, incidents that
slip through at the start of a pause, and the report section."""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta

import openpyxl
import pytest

from app import alerts, pauses, reports, ws
from app_alerts_test import FakeSender, add_user
from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from app_ws_test import _connect, _hb, _initial, one_loop
from shared.contract import TZ, Heartbeat


def at(**delta) -> str:
    return (datetime.now(TZ) + timedelta(**delta)).isoformat(timespec="seconds")


def create(ctx, user="manager", **body):
    return ctx.client.post("/api/pauses", json={"reason": "cleaning", **body}, headers=ctx.headers(user))


def end(ctx, pause_id, user="manager"):
    return ctx.client.post(f"/api/pauses/{pause_id}/end", headers=ctx.headers(user))


def insert(ctx, camera_id=1, reason="cleaning", starts=None, ends=None, ended=None, note=None) -> int:
    """A pause row written directly (for history in the past, which the API does not allow)."""
    return ctx.execute(
        "INSERT INTO monitoring_pauses (camera_id, reason, note, starts_at, ends_at, created_by, created_at, ended_by, "
        "ended_at) VALUES (:c, :r, :n, :s, :e, :u, :s, :eb, :ea) RETURNING pause_id",
        c=camera_id, r=reason, n=note, s=starts or at(minutes=-10), e=ends, u=ctx.ids["admin"],
        eb=ctx.ids["admin"] if ended else None, ea=ended)


# ---------------------------------------------------------------- create / end, roles

@pytest.mark.parametrize("user", ["admin", "manager", "tech"])
def test_every_role_can_pause_and_resume(ctx, user):
    r = create(ctx, user, camera_id=1, duration_minutes=30)
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["camera_name"], p["reason_label"], p["state"]) == ("Camera 1", "Cleaning", "active")
    assert p["created_by_name"] == user.title() and p["ends_at"] is not None
    r = end(ctx, p["pause_id"], user)
    assert r.status_code == 200 and r.json()["state"] == "ended" and r.json()["ended_by_name"] == user.title()


def test_login_required(ctx):
    assert ctx.client.post("/api/pauses", json={"reason": "cleaning"}).status_code == 401
    assert ctx.client.post("/api/pauses/1/end").status_code == 401
    assert ctx.client.get("/api/pauses").status_code == 401
    assert ctx.client.get("/api/monitoring/status").status_code == 401


def test_all_cameras_until_resumed(ctx):
    p = create(ctx, camera_id=None, reason="closed").json()
    assert p["camera_id"] is None and p["camera_name"] == "All cameras" and p["ends_at"] is None
    assert p["reason_label"] == "Restaurant closed / off day"


@pytest.mark.parametrize("body, message", [
    ({"reason": "other"}, "Write a short note when the reason is Other."),
    ({"reason": "other", "note": "   "}, "Write a short note when the reason is Other."),
    ({"camera_id": 99}, "Camera 99 does not exist."),
    ({"starts_at": "PAST"}, "A pause cannot start in the past."),
    ({"ends_at": "NOW"}, "The end must be after the start."),
    ({"duration_minutes": 30, "ends_at": "LATER"}, "Give either an end time or a duration, not both."),
    ({"ends_at": "8 DAYS"}, "A pause can last at most 7 days."),
])
def test_validation(ctx, body, message):
    times = {"PAST": at(hours=-1), "NOW": at(seconds=-1), "LATER": at(hours=1), "8 DAYS": at(days=8)}
    body = {k: times.get(v, v) if isinstance(v, str) else v for k, v in body.items()}
    r = ctx.client.post("/api/pauses", json={"reason": "cleaning", **body}, headers=ctx.headers("admin"))
    assert r.status_code == 422 and r.json()["detail"] == message


def test_unknown_reason_and_extra_fields_refused(ctx):
    h = ctx.headers("admin")
    assert ctx.client.post("/api/pauses", json={"reason": "lunch"}, headers=h).status_code == 422
    assert ctx.client.post("/api/pauses", json={"reason": "cleaning", "x": 1}, headers=h).status_code == 422


def test_other_with_a_note_is_fine(ctx):
    p = create(ctx, camera_id=2, reason="other", note=" Pest control visit ").json()
    assert p["note"] == "Pest control visit" and p["reason_label"] == "Other"


# ---------------------------------------------------------------- overlap

def test_overlapping_pause_for_the_same_camera_is_refused(ctx):
    first = create(ctx, camera_id=1, duration_minutes=60).json()
    r = create(ctx, camera_id=1, duration_minutes=30)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Camera 1 is already paused (Cleaning) until ")
    assert "End or cancel that pause first" in r.json()["detail"]
    assert create(ctx, camera_id=None).status_code == 409          # all cameras would include camera 1
    assert create(ctx, camera_id=2, duration_minutes=30).status_code == 201
    # touching ends are fine: the next pause starts when the first one ends
    assert create(ctx, camera_id=1, starts_at=first["ends_at"], duration_minutes=15).status_code == 201


def test_camera_pause_inside_an_all_cameras_pause_is_refused(ctx):
    create(ctx, camera_id=None, reason="closed")
    r = create(ctx, camera_id=2)
    assert r.status_code == 409 and r.json()["detail"].startswith("All cameras are already paused (Restaurant closed")


def test_planned_pause_overlap_says_will_be_paused(ctx):
    create(ctx, camera_id=1, starts_at=at(hours=2), duration_minutes=60)
    r = create(ctx, camera_id=1, starts_at=at(hours=2, minutes=30), duration_minutes=60)
    assert r.status_code == 409 and r.json()["detail"].startswith("Camera 1 will be paused (Cleaning) from ")


def test_ended_or_cancelled_pauses_do_not_block(ctx):
    p = create(ctx, camera_id=1).json()
    end(ctx, p["pause_id"])
    assert create(ctx, camera_id=1, duration_minutes=10).status_code == 201


# ---------------------------------------------------------------- planned pauses, resume, cancel

def test_future_pause_is_planned_and_can_be_cancelled(ctx):
    p = create(ctx, camera_id=1, starts_at=at(hours=2), duration_minutes=60).json()
    assert p["state"] == "planned"
    status = {s["camera_id"]: s for s in ctx.client.get("/api/monitoring/status", headers=ctx.headers("tech")).json()}
    assert status[1]["state"] == "planned" and status[1]["next_pause"]["pause_id"] == p["pause_id"]
    assert status[1]["pause"] is None and status[2]["state"] == "active"
    r = end(ctx, p["pause_id"])
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert ctx.audit("pause_end")[-1]["details"]["action"] == "cancelled"


def test_resume_twice_is_409_and_unknown_is_404(ctx):
    p = create(ctx, camera_id=1).json()
    assert end(ctx, p["pause_id"]).status_code == 200
    r = end(ctx, p["pause_id"])
    assert r.status_code == 409 and r.json()["detail"] == "This pause has already ended."
    assert end(ctx, 999).status_code == 404


def test_audit_rows(ctx):
    p = create(ctx, "tech", camera_id=2, reason="maintenance", duration_minutes=45).json()
    end(ctx, p["pause_id"], "admin")
    made, ended = ctx.audit("pause_create")[-1], ctx.audit("pause_end")[-1]
    assert made["user_id"] == ctx.ids["tech"] and made["target_id"] == p["pause_id"]
    assert made["details"]["reason"] == "maintenance" and made["details"]["camera_id"] == 2
    assert ended["user_id"] == ctx.ids["admin"] and ended["details"]["action"] == "resumed"


# ---------------------------------------------------------------- worker reload

def test_reload_after_create_and_end(ctx):
    r = create(ctx, camera_id=1)
    assert r.headers["X-RADS-Worker-Reload"] == "ok" and ctx.worker.reloads == 1
    r = end(ctx, r.json()["pause_id"])
    assert r.headers["X-RADS-Worker-Reload"] == "ok" and ctx.worker.reloads == 2


def test_cancel_reloads_and_every_change_is_audited_with_user_time_and_reason(ctx):
    before = datetime.now(TZ).replace(microsecond=0)
    planned = create(ctx, "tech", camera_id=1, reason="closed", starts_at=at(hours=2), duration_minutes=30)
    assert planned.json()["state"] == "planned" and ctx.worker.reloads == 1
    r = end(ctx, planned.json()["pause_id"], "manager")                   # cancel the planned pause
    assert r.json()["state"] == "cancelled"
    assert r.headers["X-RADS-Worker-Reload"] == "ok" and ctx.worker.reloads == 2
    active = create(ctx, "admin", camera_id=2, reason="maintenance")
    end(ctx, active.json()["pause_id"], "tech")                           # resume an active one
    assert ctx.worker.reloads == 4

    rows = ctx.audit("pause_create") + ctx.audit("pause_end")
    got = [(r["action"], r["user_id"], r["target_id"], r["details"]["reason"], r["details"].get("action"))
           for r in rows]
    assert got == [
        ("pause_create", ctx.ids["tech"], planned.json()["pause_id"], "closed", None),
        ("pause_create", ctx.ids["admin"], active.json()["pause_id"], "maintenance", None),
        ("pause_end", ctx.ids["manager"], planned.json()["pause_id"], "closed", "cancelled"),
        ("pause_end", ctx.ids["tech"], active.json()["pause_id"], "maintenance", "resumed"),
    ]
    for row in rows:  # when it happened
        assert before <= datetime.fromisoformat(row["created_at"]) <= datetime.now(TZ)


def test_worker_down_still_saves(ctx):
    ctx.worker.down = True
    r = create(ctx, camera_id=1)
    assert r.status_code == 201 and r.headers["X-RADS-Worker-Reload"] == "failed"
    assert ctx.sql("SELECT COUNT(*) AS n FROM monitoring_pauses")[0]["n"] == 1
    r = end(ctx, r.json()["pause_id"])
    assert r.status_code == 200 and r.headers["X-RADS-Worker-Reload"] == "failed"


# ---------------------------------------------------------------- history and status

def test_history_newest_first_with_filters(ctx):
    old = insert(ctx, 1, "closed", starts=at(days=-3), ends=at(days=-3, hours=8))
    cancelled = insert(ctx, 2, "cleaning", starts=at(days=-2), ended=at(days=-2, hours=-1))
    everywhere = insert(ctx, None, "maintenance", starts=at(hours=-1), ends=at(hours=1))
    planned = insert(ctx, 2, "no_food_prep", starts=at(days=1), ends=at(days=1, hours=2))
    h = ctx.headers("manager")
    rows = ctx.client.get("/api/pauses", headers=h).json()
    assert [r["pause_id"] for r in rows] == [planned, everywhere, cancelled, old]
    assert [r["state"] for r in rows] == ["planned", "active", "cancelled", "ended"]
    assert rows[1]["camera_name"] == "All cameras" and rows[1]["created_by_name"] == "Admin"
    assert rows[0]["reason_label"] == "No food preparation"

    def ids(**q):
        return [r["pause_id"] for r in ctx.client.get("/api/pauses", params=q, headers=h).json()]
    assert ids(active="true") == [everywhere]
    assert ids(active="false") == [planned, cancelled, old]
    assert ids(state="planned") == [planned]
    assert ids(camera_id=1) == [everywhere, old]  # all-cameras pauses included
    today = datetime.now(TZ).date()
    assert ids(**{"from": str(today - timedelta(days=3)), "to": str(today - timedelta(days=2))}) == [cancelled, old]
    assert ids(**{"from": str(today + timedelta(days=1))}) == [planned]


def test_status_shows_reason_until_and_who(ctx):
    p = create(ctx, "tech", camera_id=2, reason="maintenance", duration_minutes=90).json()
    rows = ctx.client.get("/api/monitoring/status", headers=ctx.headers("manager")).json()
    cam1, cam2 = rows
    assert cam1["state"] == "active" and cam1["pause"] is None
    assert cam2["state"] == "paused" and cam2["pause"]["pause_id"] == p["pause_id"]
    assert (cam2["pause"]["reason_label"], cam2["pause"]["ends_at"], cam2["pause"]["created_by_name"]) == \
        ("Maintenance / technical work", p["ends_at"], "Tech")


def test_status_uses_the_shared_rule_for_all_cameras_pauses(ctx):
    create(ctx, camera_id=None, reason="closed")
    assert [s["state"] for s in ctx.client.get("/api/monitoring/status", headers=ctx.headers("tech")).json()] == \
        ["paused", "paused"]


# ---------------------------------------------------------------- WebSocket

def test_create_and_end_broadcast_monitoring_changed(ctx):
    with one_loop(ctx.local), _connect(ctx) as sock:
        _initial(sock)
        h = ctx.headers("manager")
        p = ctx.local.post("/api/pauses", json={"camera_id": 2, "reason": "cleaning"}, headers=h).json()
        assert sock.receive_json() == {"type": "monitoring.changed", "change": "created", "pause_id": p["pause_id"],
                                       "camera_id": 2}
        ctx.local.post(f"/api/pauses/{p['pause_id']}/end", headers=h)
        assert sock.receive_json()["change"] == "ended"


def test_paused_heartbeat_is_forwarded(ctx):
    with one_loop(ctx.local), _connect(ctx) as sock:
        _initial(sock)
        body = {**_hb(1), "detection": "paused", "pause_reason": "cleaning"}
        ctx.local.post("/internal/heartbeat", json=body, headers=INTERNAL)
        msg = sock.receive_json()
        assert msg["type"] == "camera.status" and msg["detection"] == "paused" and msg["pause_reason"] == "cleaning"


def test_offline_message_keeps_the_last_known_detection():
    clock = [100.0]
    mon = ws.CameraMonitor(clock=lambda: clock[0], offline_after=15)
    mon.record(Heartbeat(camera_id=1, status="online", fps=5, ts=at(), detection="paused", pause_reason="closed"))
    mon.record(Heartbeat(camera_id=2, status="online", fps=5, ts=at()))
    clock[0] += 16
    gone = {m.camera_id: m for m in mon.newly_offline()}
    assert gone[1].status == "offline" and gone[1].detection == "paused" and gone[1].pause_reason == "closed"
    assert gone[2].detection == "active" and gone[2].pause_reason is None
    assert mon.status_of(1).detection == "paused"
    assert mon.status_of(5).detection == "active"  # never seen: nothing known


# ---------------------------------------------------------------- the 1-minute job

class CountingWorker:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1
        return True


def test_transition_job_announces_starts_and_ends(ctx, monkeypatch):
    monkeypatch.setattr(pauses, "watch", pauses.TransitionWatch())
    t0 = datetime.now(TZ).replace(microsecond=0)
    starting = insert(ctx, 1, starts=(t0 + timedelta(seconds=30)).isoformat(), ends=(t0 + timedelta(hours=1)).isoformat())
    ending = insert(ctx, 2, starts=(t0 - timedelta(hours=1)).isoformat(), ends=(t0 + timedelta(seconds=40)).isoformat())
    worker, sent = CountingWorker(), []
    assert pauses.run_transition_job(ctx.Session, worker, sent.append, now=t0) == []  # first run: just remembers
    events = pauses.run_transition_job(ctx.Session, worker, sent.append, now=t0 + timedelta(minutes=1))
    assert {(e["pause_id"], e["change"]) for e in events} == {(starting, "started"), (ending, "finished")}
    assert sent == events and all(e["type"] == "monitoring.changed" for e in sent)
    assert worker.reloads == 1  # once, however many pauses changed
    assert pauses.run_transition_job(ctx.Session, worker, sent.append, now=t0 + timedelta(minutes=2)) == []
    assert worker.reloads == 1


def test_transition_job_ignores_pauses_ended_by_hand(ctx, monkeypatch):
    monkeypatch.setattr(pauses, "watch", pauses.TransitionWatch())
    t0 = datetime.now(TZ).replace(microsecond=0)
    insert(ctx, 1, starts=(t0 + timedelta(seconds=30)).isoformat(), ended=t0.isoformat())  # cancelled
    worker = CountingWorker()
    pauses.run_transition_job(ctx.Session, worker, lambda m: None, now=t0)
    assert pauses.run_transition_job(ctx.Session, worker, lambda m: None, now=t0 + timedelta(minutes=1)) == []
    assert worker.reloads == 0


def test_transition_job_is_scheduled(ctx):
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.main import add_jobs
    from shared.config import get_settings
    sched = BackgroundScheduler(timezone="Asia/Karachi")
    add_jobs(sched, ctx.Session, get_settings())
    job = sched.get_job("pause_transitions")
    assert job.func is pauses.run_transition_job and job.trigger.interval == timedelta(minutes=1)


# ---------------------------------------------------------------- incidents during a pause

@pytest.fixture
def phone(ctx):
    sender = FakeSender()
    ctx.senders.append(sender)
    add_user(ctx, "m1", "Manager", "+92 300 1111111")
    add_user(ctx, "a1", "Admin", "+92 300 2222222")
    return sender


def test_incident_arriving_during_a_pause_is_kept_without_texts(ctx, phone, caplog):
    create(ctx, camera_id=1)
    paused_iid = ctx.add_incident("H-01", 1, at())
    other_iid = ctx.add_incident("H-01", 2, at())
    with caplog.at_level(logging.WARNING, logger="rads.app.internal"):
        assert ctx.local.post("/internal/incidents/new", json={"incident_id": paused_iid},
                              headers=INTERNAL).status_code == 200
    assert ctx.sql("SELECT channel FROM alerts WHERE incident_id = :i", i=paused_iid) == [{"channel": "dashboard"}]
    assert phone.sent == []
    assert any("arrived during pause" in r.getMessage() for r in caplog.records)
    ctx.local.post("/internal/incidents/new", json={"incident_id": other_iid}, headers=INTERNAL)
    assert len(phone.sent) == 1  # camera 2 was not paused


def test_catch_up_and_escalation_do_not_text_for_incidents_during_a_pause(ctx, phone):
    insert(ctx, 1, starts=at(minutes=-30), ends=at(minutes=-20))   # a pause that is over now
    during = ctx.add_incident("H-01", 1, at(minutes=-25))           # happened inside it
    alerts.run_minute_jobs(ctx.Session)
    kinds = [r["kind"] for r in ctx.sql("SELECT kind FROM alerts WHERE incident_id = :i ORDER BY alert_id", i=during)]
    assert kinds == ["first", "escalation"]  # both on the dashboard
    assert phone.sent == []                  # no first text, no escalation text


# ---------------------------------------------------------------- reports

@pytest.fixture
def report_pauses(ctx):
    insert(ctx, 1, "cleaning", starts="2026-12-01T10:00:00+05:00", ends="2026-12-01T12:00:00+05:00")
    insert(ctx, None, "closed", starts="2026-12-02T08:00:00+05:00", ends="2026-12-02T09:00:00+05:00")
    insert(ctx, 2, "maintenance", starts="2026-12-03T23:00:00+05:00", ends="2026-12-04T03:00:00+05:00")  # crosses the end
    insert(ctx, 1, "cleaning", starts="2026-12-02T15:00:00+05:00", ended="2026-12-02T14:00:00+05:00")    # cancelled
    insert(ctx, 1, "other", starts="2026-11-20T08:00:00+05:00", ends="2026-11-20T09:00:00+05:00",
           note="before the period")


def test_report_data_lists_pauses_and_hours_per_camera(ctx, report_pauses):
    with ctx.Session() as db:
        d = reports.collect(db, date(2026, 12, 1), date(2026, 12, 3), "Admin (admin)",
                            now=datetime(2026, 12, 10, tzinfo=TZ))
    assert [(p["camera"], p["reason"]) for p in d.pauses] == [
        ("Camera 1", "Cleaning"), ("All cameras", "Restaurant closed / off day"),
        ("Camera 2", "Maintenance / technical work")]
    assert [round(p["in_period"].total_seconds() / 3600, 2) for p in d.pauses] == [2.0, 1.0, 1.0]  # clipped at 24:00
    assert d.paused_hours == [("Camera 1", 3.0), ("Camera 2", 2.0)]  # the all-cameras hour counts for both


def test_report_counts_a_running_pause_only_up_to_now(ctx):
    insert(ctx, 1, starts="2026-12-01T10:00:00+05:00", ends=None)
    with ctx.Session() as db:
        d = reports.collect(db, date(2026, 12, 1), date(2026, 12, 1), "x", now=datetime(2026, 12, 1, 13, tzinfo=TZ))
    assert d.paused_hours[0] == ("Camera 1", 3.0) and d.pauses[0]["running"]
    assert reports.pause_end_text(d.pauses[0]) == "until resumed (still paused)"


def test_pdf_has_the_monitoring_paused_section(ctx, report_pauses, tmp_path, monkeypatch):
    from reportlab import rl_config
    monkeypatch.setattr(rl_config, "pageCompression", 0)  # readable text in the PDF bytes
    with ctx.Session() as db:
        d = reports.collect(db, date(2026, 12, 1), date(2026, 12, 3), "Admin", now=datetime(2026, 12, 10, tzinfo=TZ))
    out = tmp_path / "r.pdf"
    reports.write_pdf(d, out)
    pdf = out.read_bytes()
    for needle in (b"6. Monitoring paused", b"No incidents are detected while monitoring is paused",
                   b"All cameras", b"3 h 00 min", b"7. Incident list"):
        assert needle in pdf, needle


def test_xlsx_has_the_monitoring_paused_sheet_and_summary(ctx, report_pauses, tmp_path):
    with ctx.Session() as db:
        d = reports.collect(db, date(2026, 12, 1), date(2026, 12, 3), "Admin", now=datetime(2026, 12, 10, tzinfo=TZ))
    out = tmp_path / "r.xlsx"
    reports.write_xlsx(d, out)
    wb = openpyxl.load_workbook(out)
    sheet = wb["Monitoring paused"]
    rows = [[c.value for c in r] for r in sheet.iter_rows(min_row=5, max_col=7)]
    assert [(r[0], r[1], r[5]) for r in rows] == [("Camera 1", "Cleaning", 2.0),
                                                  ("All cameras", "Restaurant closed / off day", 1.0),
                                                  ("Camera 2", "Maintenance / technical work", 1.0)]
    assert "No incidents are detected while monitoring is paused" in sheet["A2"].value
    summary = [[c.value for c in r] for r in wb["Summary"].iter_rows(max_col=2)]
    i = summary.index(["Monitoring paused (hours in this period)", None])
    assert summary[i + 2: i + 4] == [["Camera 1", 3.0], ["Camera 2", 2.0]]


def test_report_endpoint_includes_pauses(ctx):
    start = datetime.now(TZ).date() - timedelta(days=3)
    insert(ctx, 2, "cleaning", starts=f"{start}T10:00:00+05:00", ends=f"{start}T10:30:00+05:00")
    r = ctx.client.post("/api/reports", json={"period_start": str(start), "period_end": str(start), "format": "xlsx"},
                        headers=ctx.headers("manager"))
    assert r.status_code == 201
    dl = ctx.client.get(r.json()["download_url"], headers=ctx.headers("manager"))
    wb = openpyxl.load_workbook(io.BytesIO(dl.content))
    assert wb["Monitoring paused"]["A5"].value == "Camera 2" and wb["Monitoring paused"]["F5"].value == 0.5


# ---------------------------------------------------------------- the fake worker obeys pauses like the real one

def _fake(ctx):
    import random

    from app_fake_incidents_test import FakeClock, SpyNotifier
    from scripts.fake_incidents import FakeWorker
    notifier = SpyNotifier(ctx.local, INTERNAL["X-RADS-Internal"], retry_delay=0)
    return FakeWorker(ctx.Session, ctx.evidence_dir, notifier, random.Random(3), clock=FakeClock()), notifier


def test_fake_worker_makes_no_incidents_on_a_paused_camera(ctx):
    w, _ = _fake(ctx)
    insert(ctx, 1, starts=at(minutes=-1))
    cams = {ctx.sql("SELECT camera_id FROM incidents WHERE incident_id = :i", i=w.new_incident())[0]["camera_id"]
            for _ in range(8)}
    assert cams == {2}
    insert(ctx, 2, starts=at(minutes=-1))
    assert w.new_incident() is None  # every camera paused: nothing


def test_fake_worker_closes_open_incidents_when_a_pause_starts(ctx):
    w, _ = _fake(ctx)
    iid = None
    while iid is None or w.camera_of[iid] != 1:
        iid = w.new_incident()
    insert(ctx, 1, starts=at(minutes=-1))
    w.close_due()
    assert iid not in w.open and ctx.sql("SELECT end_time FROM incidents WHERE incident_id = :i", i=iid)[0]["end_time"]


def test_fake_worker_heartbeat_says_paused(ctx):
    w, _ = _fake(ctx)
    sent = []
    w.notifier.heartbeat = sent.append
    insert(ctx, 2, "no_food_prep", starts=at(minutes=-1))
    w.heartbeats()
    by_cam = {hb["camera_id"]: hb for hb in sent}
    assert (by_cam[1]["detection"], by_cam[1]["pause_reason"]) == ("active", None)
    assert (by_cam[2]["detection"], by_cam[2]["pause_reason"]) == ("paused", "no_food_prep")


def test_fake_worker_live_view_shows_the_banner_but_the_raw_picture_stays_clean():
    from PIL import Image

    from scripts.fake_incidents import render_frame
    cam = {"name": "Camera 1", "frame_width": 2688, "frame_height": 1520}
    with Image.open(io.BytesIO(render_frame(cam, [], 1, paused="Cleaning"))) as live:
        r, g, b = live.getpixel((5, live.height - 5))
        assert r > 150 and g < 120 and b < 60  # the amber banner along the bottom
    with Image.open(io.BytesIO(render_frame(cam, [], 1, raw=True, paused="Cleaning"))) as raw:
        r, g, b = raw.getpixel((5, raw.height - 5))
        assert not (r > 150 and g < 120 and b < 60)
