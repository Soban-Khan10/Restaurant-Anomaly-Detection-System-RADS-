"""Pause monitoring, worker side: active_pause() polling, closing at the pause start, no detection while paused,
fresh state after resume, all-cameras pauses, heartbeats, /health, the banner (not on ?raw=1), errors."""
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytest

from shared.contract import TZ, CameraStatus, DetectionState, PauseReason
from shared.db import create_schema, make_engine
from worker.main import OFFLINE_CLOSE_S, CameraPipeline, PerfCsv, Scheduler, build_engines, zone_object_classes
from worker.notifier import CloseJob, HeartbeatJob, Notifier, OpenJob
from worker.pauses import Pause, PauseWatcher
from worker.reader import Frame
from worker.settings import load_settings
from worker.webserver import FrameHub

FPS, DT = 5.0, 0.2
IMG = np.full((1520, 2688, 3), 90, np.uint8)


class FakeReader:
    is_file = False

    def __init__(self):
        self.status, self.fps, self.frame = CameraStatus.ONLINE, 25.0, None

    def latest(self):
        return self.frame

    def stop(self):
        pass


class FakeDetector:
    device = "cpu"

    def __init__(self):
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return [{"cls_name": "person", "conf": 0.9, "xyxy": (1000.0, 300.0, 1300.0, 1400.0)},
                {"cls_name": "mask_off", "conf": 0.8, "xyxy": (1110.0, 400.0, 1190.0, 450.0)}]


class FakeNotifier:
    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)

    def of(self, kind):
        return [j for j in self.jobs if isinstance(j, kind)]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rads.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO roles (role_id, name) VALUES (1, 'Admin');
        INSERT INTO users (user_id, role_id, username, password_hash, created_at)
            VALUES (1, 1, 'admin', 'x', '2026-11-25T00:00:00+05:00');
        INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (1, 'Camera 1', 'rtsp://a'), (2, 'Camera 2', 'rtsp://b');
        INSERT INTO zones (zone_id, camera_id, name, type, polygon) VALUES
            (1, 1, 'Prep area 1', 'prep', '[[0,0],[2688,0],[2688,1520],[0,1520]]'),
            (2, 2, 'Prep area 2', 'prep', '[[0,0],[2688,0],[2688,1520],[0,1520]]');
        INSERT INTO hazards VALUES ('H-08', 'Mask missing', 'mask_off', 'mask_on', 'worker');
        INSERT INTO rules (rule_id, hazard_id, camera_id) VALUES (1, 'H-08', 1), (2, 'H-08', 2);
        INSERT INTO rule_zones VALUES (1, 1), (2, 2);
    """)
    con.commit()
    con.close()
    engine = make_engine(path)
    yield engine
    engine.dispose()


def iso(dt):
    return dt.isoformat(timespec="seconds")


def add_pause(db, camera_id, starts, ends=None, reason="cleaning"):
    with db.begin() as con:
        return con.exec_driver_sql(
            "INSERT INTO monitoring_pauses (camera_id, reason, starts_at, ends_at, created_by, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?) RETURNING pause_id",
            (camera_id, reason, iso(starts), iso(ends) if ends else None, iso(datetime.now(TZ)))).scalar_one()


def end_pause(db, pause_id):
    with db.begin() as con:
        con.exec_driver_sql("UPDATE monitoring_pauses SET ended_at = ?, ended_by = 1 WHERE pause_id = ?",
                            (iso(datetime.now(TZ)), pause_id))


class Clock:
    def __init__(self):
        self.now = datetime.now(TZ)


@pytest.fixture
def sched(db):
    ws = load_settings(db)
    cams = (1, 2)
    pipelines = {c: CameraPipeline(c, ws.zones_for(c), FPS, zone_object_classes(ws)) for c in cams}
    engines = build_engines(ws, pipelines, FPS)
    readers = {c: FakeReader() for c in cams}
    clock = Clock()
    watcher = PauseWatcher(db, cams, now=lambda: clock.now)
    hub = FrameHub(cams)
    s = Scheduler(readers, FakeDetector(), pipelines, interval=DT, show=False, display_width=960, report_every=30,
                  duration=0, stop=threading.Event(), engines=engines, notifier=FakeNotifier(), settings=ws,
                  hub=hub, pauses=watcher)
    s.clock, s.seq = clock, 0
    s.base = time.perf_counter() - 100.0

    def step(n, cams=(2,)):
        for _ in range(n):
            s.seq += 1
            for c in cams:
                readers[c].frame = Frame(IMG, s.base + s.seq * DT, s.seq, 0, 1)
                s._process(c, s._window, s._total)

    s.step, s.check = step, (lambda: s._check_pauses(force=True))
    return s


def opens(s):
    return s.notifier.of(OpenJob)


def test_pause_closes_the_open_incident_at_the_pause_start(sched, db, caplog):
    caplog.set_level(logging.INFO)
    s = sched
    s.step(15)                                                   # mask off: H-08 opens (13 frames)
    assert len(opens(s)) == 1 and s.engines[2].open_incident_count() == 1
    start = (s.clock.now - timedelta(seconds=3)).replace(microsecond=0)   # stored to the second
    add_pause(db, 2, start, start + timedelta(minutes=30))
    s.check()
    closes = [j.event for j in s.notifier.of(CloseJob)]
    assert [(c.hazard_id, c.reason) for c in closes] == [("H-08", "detection paused")]
    assert closes[0].end_t == start.timestamp()                  # end_time = pause start
    assert s.engines[2].open_incident_count() == 0 and not s.engines[2]._states and not s.engines[2]._spots
    assert s.detection(2) == DetectionState.PAUSED and s.detection(1) == DetectionState.ACTIVE
    msg = next(r.getMessage() for r in caplog.records if "detection PAUSED" in r.getMessage())
    assert "cam2" in msg and "Cleaning" in msg and "until" in msg and "closed 1 open incident" in msg


def test_pause_noticed_late_never_ends_an_incident_before_it_started(sched, db):
    s = sched
    s.base = time.perf_counter() - 3.0 - 15 * DT                 # the incident starts ~1 s ago ...
    s.step(15)
    start = (s.clock.now - timedelta(seconds=30)).replace(microsecond=0)   # ... after the pause's start
    add_pause(db, 2, start)
    s.check()
    (close,) = [j.event for j in s.notifier.of(CloseJob)]
    inc = s.notifier.of(OpenJob)[0].event
    assert close.end_t == inc.start_t > start.timestamp()


def test_no_incidents_and_no_detector_while_paused(sched, db):
    s = sched
    add_pause(db, 2, s.clock.now - timedelta(seconds=1))
    s.check()
    calls = s.detector.calls
    s.step(60)                                                   # 12 s of violations on camera 2
    assert opens(s) == [] and s.detector.calls == calls          # detector not even run
    assert s._window.summary(time.perf_counter())["cams"][2]["paused"] == 60
    s.step(15, cams=(1,))                                        # camera 1 is not paused
    assert [j.event.camera_id for j in opens(s)] == [1]


def test_resume_starts_with_fresh_state(sched, db, caplog):
    caplog.set_level(logging.INFO)
    s = sched
    s.step(10)                                                   # 10 violation frames before the pause ...
    pid = add_pause(db, 2, s.clock.now - timedelta(seconds=1))
    s.check()
    s.step(20)
    end_pause(db, pid)                                           # resumed early from the dashboard
    s.check()
    assert s.detection(2) == DetectionState.ACTIVE
    assert any("cam2: detection RESUMED (pause" in r.getMessage() for r in caplog.records)
    s.step(12)                                                   # ... do not count: 12 < 13 after the resume
    assert opens(s) == []
    s.step(1)
    assert len(opens(s)) == 1


def test_planned_pause_starts_on_time(sched, db):
    s = sched
    start = s.clock.now + timedelta(minutes=10)
    add_pause(db, 2, start, start + timedelta(minutes=5), reason="closed")
    s.check()
    assert s.detection(2) == DetectionState.ACTIVE               # not yet
    s.clock.now = start - timedelta(seconds=1)
    s.check()
    assert s.detection(2) == DetectionState.ACTIVE
    s.clock.now = start
    s.check()
    assert s.detection(2) == DetectionState.PAUSED               # from its start ...
    s.clock.now = start + timedelta(minutes=5)
    s.check()
    assert s.detection(2) == DetectionState.ACTIVE               # ... until its (exclusive) end


def test_all_cameras_pause_pauses_both(sched, db):
    s = sched
    add_pause(db, None, s.clock.now - timedelta(seconds=1), reason="closed")
    s.check()
    assert s.detection(1) == s.detection(2) == DetectionState.PAUSED
    assert all(p.all_cameras for p in s._paused.values())
    s.step(20, cams=(1, 2))
    assert opens(s) == []


def test_watcher_polls_every_second():
    t = [0.0]
    calls = []

    class Eng:
        def connect(self):
            class C:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False
            return C()

    w = PauseWatcher(Eng(), [1, 2], clock=lambda: t[0], query=lambda con, c, now: calls.append(c))
    assert w.interval_s == 1.0
    assert w.poll() and calls == [1, 2]                          # at once, one read per camera
    t[0] = 0.9
    assert not w.poll() and len(calls) == 2                      # cached
    t[0] = 1.0
    assert w.poll() and len(calls) == 4
    w.request()                                                  # /reload
    assert w.poll() and len(calls) == 6


def test_heartbeats_and_health_carry_the_pause(sched, db):
    s = sched
    add_pause(db, 2, s.clock.now - timedelta(seconds=1), s.clock.now.replace(hour=23, minute=59, second=0) +
              timedelta(seconds=0), reason="maintenance")
    s.check()
    now = time.perf_counter()
    for c in (1, 2):
        s._hb_next[c], s._hb_last[c] = 0.0, now - 5
    s._heartbeats(now)
    hb = {j.camera_id: j for j in s.notifier.of(HeartbeatJob)}
    assert (hb[2].detection, hb[2].pause_reason) == (DetectionState.PAUSED, PauseReason.MAINTENANCE)
    assert (hb[1].detection, hb[1].pause_reason) == (DetectionState.ACTIVE, None)
    assert hb[2].status == CameraStatus.ONLINE                   # paused is not offline
    s._health_next = 0.0
    s._publish_health(now)
    h = s.hub.health()["cameras"]
    assert h[2]["detection"] == "paused" and h[2]["pause_reason"] == "maintenance"
    assert h[2]["pause_label"] == "Maintenance / technical work" and h[2]["pause_until"].endswith("23:59:00+05:00")
    assert h[1]["detection"] == "active" and h[1]["pause_reason"] is None and h[2]["status"] == "online"


def test_heartbeat_message_has_detection_fields(db, tmp_path):
    import json

    import httpx
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(json.loads(r.content)) or
                                                       httpx.Response(200, json={"ok": True})))
    n = Notifier(evidence_dir=tmp_path / "ev", api_url="http://127.0.0.1:8000", token="t", model_version="m",
                 db_engine=db, http_client=client)
    n.start(close_stale=False)
    n.submit(HeartbeatJob(2, CameraStatus.ONLINE, 0.0, time.time(), time.time(), DetectionState.PAUSED,
                          PauseReason.CLEANING))
    n.submit(HeartbeatJob(1, CameraStatus.ONLINE, 5.0, time.time(), time.time()))
    assert n.stop(10)
    by_cam = {b["camera_id"]: b for b in sent}
    assert (by_cam[2]["detection"], by_cam[2]["pause_reason"]) == ("paused", "cleaning")
    assert (by_cam[1]["detection"], by_cam[1]["pause_reason"]) == ("active", None)


def test_paused_camera_never_triggers_the_offline_close(sched, db):
    s = sched
    s.step(15)
    add_pause(db, 2, s.clock.now - timedelta(seconds=1))
    s.check()
    n_closes = len(s.notifier.of(CloseJob))                      # the pause's own close
    now = time.perf_counter()
    for dt in (30, OFFLINE_CLOSE_S + 1, 300):                    # minutes without processing a single frame
        s._check_offline(now + dt)
    assert len(s.notifier.of(CloseJob)) == n_closes and 2 not in s._offline
    assert s.status(2) == CameraStatus.ONLINE


def test_stream_banner_and_clean_raw_stream(sched, db):
    s = sched
    add_pause(db, 2, s.clock.now - timedelta(seconds=1), reason="cleaning")
    s.check()
    s.step(1)
    seq, ann = s.hub._encode(2)
    seq, raw = s.hub._encode(2, raw=True)
    a = cv2.imdecode(np.frombuffer(ann, np.uint8), cv2.IMREAD_COLOR)
    r = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert a.shape == r.shape == (760, 1344, 3)
    assert np.abs(r.astype(int) - 90).max() <= 3                 # raw: the plain frame, no banner
    top, rest = np.abs(a.astype(int) - 90).max(axis=2)[:30], np.abs(a.astype(int) - 90).max(axis=2)[80:]
    assert (top > 30).mean() > 0.9 and rest.max() <= 3           # banner across the top, nothing else drawn
    assert Pause.from_row({"pause_id": 1, "reason": "cleaning", "starts_at": iso(s.clock.now), "ends_at": None,
                           "camera_id": 2}).text() == "DETECTION PAUSED – Cleaning – until resumed"
    assert s._paused[2].text("-").startswith("DETECTION PAUSED - Cleaning - until")


def test_pause_check_error_keeps_the_last_state(sched, db, caplog):
    s = sched
    add_pause(db, 2, s.clock.now - timedelta(seconds=1))
    s.check()
    assert s.detection(2) == DetectionState.PAUSED

    def broken(con, c, now):
        raise RuntimeError("database is locked")

    s.pauses.query = broken
    s.check()                                                    # no crash, still paused
    s.check()
    assert s.detection(2) == DetectionState.PAUSED and s.pauses.errors == 2
    warns = [r for r in caplog.records if "pause check failed" in r.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING  # rate-limited


def test_perf_csv_has_detection_paused(tmp_path):
    import csv
    summary = {"wall_s": 30.0, "busy_pct": 20.0, "gui_pct": 0.0, "cams": {c: {
        "processed": 0 if c == 2 else 150, "skipped": 0, "stale": 0, "errors": 0, "paused": 150 if c == 2 else 0,
        "detect_ms": {"mean": None, "p95": None}, "lag_ms": {"p50": None, "p95": None}} for c in (1, 2)}}

    class R:
        fps = 25.0

    p = PerfCsv(tmp_path / "perf.csv")
    p.write(summary, {1: R(), 2: R()}, {}, None, paused={2: object()})
    (row,) = list(csv.DictReader((tmp_path / "perf.csv").open()))
    assert row["detection_paused"] == "2" and row["cam2_paused"] == "150" and row["cam2_missed"] == "0"
