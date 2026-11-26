"""Phase 5 robustness: RTSP reader stall/reconnect, reset on reconnect, offline > 60 s closes incidents, frame-size
check, perf.csv, and the zone reload wording."""
import csv
import logging
import sqlite3
import threading
import time

import numpy as np
import pytest

from shared.contract import CameraStatus, ZoneType
from shared.db import create_schema
from shared.db import make_engine
from worker import reader as reader_mod
from worker.main import OFFLINE_CLOSE_S, CameraPipeline, PerfCsv, Scheduler, build_engines, zone_object_classes
from worker.notifier import CloseJob, HeartbeatJob
from worker.reader import CameraReader, Frame, ffmpeg_capture_options
from worker.reload import diff
from worker.settings import WorkerSettings, Zone, load_settings

FPS, DT = 5.0, 0.2
IMG = np.zeros((1520, 2688, 3), np.uint8)


# --------------------------------------------------------------------------- reader


def test_ffmpeg_options_force_tcp_and_timeout():
    assert ffmpeg_capture_options(None) == "rtsp_transport;tcp|timeout;5000000"
    merged = ffmpeg_capture_options("rtsp_transport;udp|probesize;32")
    assert merged == "rtsp_transport;tcp|probesize;32|timeout;5000000"   # TCP forced, the user's extras kept


class FakeCap:
    """A stream that sends `good` frames, then fails: quickly (EOF) or by blocking for `block_s` (dead network)."""

    def __init__(self, good=5, block_s=0.0):
        self.good, self.block_s, self.n = good, block_s, 0

    def read(self):
        self.n += 1
        if self.n <= self.good:
            time.sleep(0.01)
            return True, IMG
        time.sleep(self.block_s or 0.01)
        return False, None

    def release(self):
        pass


def fake_stream_reader(monkeypatch, caps, stall_s=0.5):
    r = CameraReader(1, "rtsp://127.0.0.1:8554/cam1", retry_s=0.1, stall_s=stall_s)
    opened = []

    def _open():
        if not caps:
            return False
        r._cap = caps.pop(0)
        r.source_fps, r.codec = 25.0, "hevc"
        opened.append(time.perf_counter())
        return True

    monkeypatch.setattr(r, "_open", _open)
    return r, opened


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_stalled_stream_goes_reconnecting_then_reconnects(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    r, opened = fake_stream_reader(monkeypatch, [FakeCap(good=5), FakeCap(good=1000)], stall_s=0.5)
    r.start()
    try:
        assert wait_for(lambda: r.status == CameraStatus.ONLINE and r.latest().conn == 1)
        t_last = time.perf_counter()
        assert wait_for(lambda: r.status == CameraStatus.RECONNECTING)
        assert time.perf_counter() - t_last < 1.5                      # ~stall_s after the last frame
        assert wait_for(lambda: r.latest().conn == 2)                  # back, as connection #2
        assert r.status == CameraStatus.ONLINE and r.connects == 2 and r.reconnects == 1
    finally:
        r.stop(timeout=3)
    msgs = [x.getMessage() for x in caplog.records]
    assert any("connected (#1): 2688x1520, source 25.00 fps, codec hevc" in m for m in msgs)
    assert any("stream stalled" in m for m in msgs)


def test_a_read_that_hits_the_timeout_is_a_stall_at_once(monkeypatch):
    monkeypatch.setattr(reader_mod, "RTSP_READ_TIMEOUT_MS", 300)
    r, _ = fake_stream_reader(monkeypatch, [FakeCap(good=3, block_s=0.3)], stall_s=60.0)
    r.start()
    try:
        assert wait_for(lambda: r.status == CameraStatus.ONLINE)
        t = time.perf_counter()
        assert wait_for(lambda: r.status == CameraStatus.RECONNECTING, timeout=3)
        assert time.perf_counter() - t < 1.0                           # not stall_s (60 s): one timed-out read
    finally:
        r.stop(timeout=3)


def test_one_stalled_reader_does_not_hold_up_another(monkeypatch):
    dead, _ = fake_stream_reader(monkeypatch, [FakeCap(good=2, block_s=0.5)] * 1, stall_s=0.5)
    live = CameraReader(2, "rtsp://127.0.0.1:8554/cam2", retry_s=0.1)
    monkeypatch.setattr(live, "_open", lambda: setattr(live, "_cap", FakeCap(good=10**6)) or True)
    dead.start()
    live.start()
    try:
        assert wait_for(lambda: dead.status == CameraStatus.RECONNECTING)
        n = live.frames
        time.sleep(0.5)
        assert live.frames > n + 10 and live.status == CameraStatus.ONLINE
    finally:
        dead.stop(timeout=3)
        live.stop(timeout=3)


# --------------------------------------------------------------------------- scheduler


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

    def detect(self, image):
        return [{"cls_name": "person", "conf": 0.9, "xyxy": (1000.0, 300.0, 1300.0, 1400.0)},
                {"cls_name": "mask_off", "conf": 0.8, "xyxy": (1110.0, 400.0, 1190.0, 450.0)}]


class FakeNotifier:
    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)

    def closes(self):
        return [j.event for j in self.jobs if isinstance(j, CloseJob)]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rads.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO cameras (camera_id, name, rtsp_url, frame_width, frame_height)
            VALUES (2, 'Camera 2', 'rtsp://x', 2688, 1520);
        INSERT INTO zones (zone_id, camera_id, name, type, polygon) VALUES
            (2, 2, 'Prep area 2', 'prep', '[[0,0],[2688,0],[2688,1520],[0,1520]]');
        INSERT INTO hazards VALUES ('H-08', 'Mask missing', 'mask_off', 'mask_on', 'worker');
        INSERT INTO rules (rule_id, hazard_id, camera_id) VALUES (2, 'H-08', 2);
        INSERT INTO rule_zones VALUES (2, 2);
    """)
    con.commit()
    con.close()
    engine = make_engine(path)
    yield engine
    engine.dispose()


def last_violation_t(engine):
    (key,) = engine.open_incidents()
    return engine._states[key].incident.last_violation_t


@pytest.fixture
def sched(db):
    ws = load_settings(db)
    pipelines = {2: CameraPipeline(2, ws.zones_for(2), FPS, zone_object_classes(ws))}
    engines = build_engines(ws, pipelines, FPS)
    reader, notifier = FakeReader(), FakeNotifier()
    s = Scheduler({2: reader}, FakeDetector(), pipelines, interval=DT, show=False, display_width=960,
                  report_every=30, duration=0, stop=threading.Event(), engines=engines, notifier=notifier,
                  settings=ws)
    s.base, s.seq = time.perf_counter() - 1000.0, 0

    def step(n, conn=1, image=IMG):
        for _ in range(n):
            s.seq += 1
            reader.frame = Frame(image, s.base + s.seq * DT, s.seq, 0, conn)
            s._process(2, s._window, s._total)

    s.step = step
    return s, reader, notifier, engines[2]


def test_reconnect_resets_the_camera_and_closes_at_the_last_violation(sched, caplog):
    caplog.set_level(logging.INFO)
    s, reader, notifier, engine = sched
    s.step(15)                                             # mask off: H-08 opens after 13 frames
    assert engine.open_incident_count() == 1
    last_violation = last_violation_t(engine)
    tracks_before = s.pipelines[2].tracker
    s.step(1, conn=2)                                      # the reader reconnected
    closes = notifier.closes()
    assert [(c.hazard_id, c.reason) for c in closes] == [("H-08", "camera reconnected")]
    assert closes[0].end_t == last_violation
    assert any("cam2: reconnected – closed 1 open incident" in r.getMessage() for r in caplog.records)
    assert engine.open_incident_count() == 0 and s.pipelines[2].tracker is tracks_before  # reset, same objects
    s.step(1, conn=2)                                      # same connection: no second reset
    assert len(notifier.closes()) == 1


def test_frame_size_different_from_the_db_is_a_warning(sched, caplog):
    s, *_ = sched
    s.step(1, image=np.zeros((1080, 1920, 3), np.uint8))
    assert any("sends 1920x1080 but the DB says 2688x1520" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    caplog.clear()
    s.step(1, conn=2)                                      # 2688x1520 = the DB size: no warning
    assert not any("DB says" in r.getMessage() for r in caplog.records)


def test_offline_more_than_60_s_closes_incidents_once(sched, caplog):
    s, reader, notifier, engine = sched
    s.step(15)
    last_violation = last_violation_t(engine)
    reader.status = CameraStatus.RECONNECTING              # unplugged after the last frame
    t_last = reader.frame.capture_t
    s._check_offline(t_last + 30)                          # 30 s: nothing closed yet
    assert notifier.closes() == [] and s.status(2) == CameraStatus.RECONNECTING
    s._check_offline(t_last + OFFLINE_CLOSE_S + 1)         # > 60 s after the last frame
    closes = notifier.closes()
    assert [(c.hazard_id, c.reason, c.end_t) for c in closes] == [("H-08", "camera offline", last_violation)]
    assert s.status(2) == CameraStatus.OFFLINE
    s._check_offline(t_last + OFFLINE_CLOSE_S + 30)        # once per outage
    assert len(notifier.closes()) == 1
    s._hb_next[2], s._hb_last[2] = 0.0, 0.0                   # the next heartbeat says offline
    s._heartbeats(t_last + OFFLINE_CLOSE_S + 31)
    hb = [j for j in notifier.jobs if isinstance(j, HeartbeatJob)][-1]
    assert hb.status == CameraStatus.OFFLINE
    reader.status = CameraStatus.ONLINE                    # back
    s._check_offline(t_last + 200)
    assert s.status(2) == CameraStatus.ONLINE
    s.step(1, conn=2)                                      # reconnect reset: nothing left to close
    assert len(notifier.closes()) == 1
    assert any("not online for 61 s" in r.getMessage() for r in caplog.records)


def test_short_outage_closes_nothing_while_down(sched):
    s, reader, notifier, engine = sched
    s.step(15)
    reader.status = CameraStatus.RECONNECTING
    for dt in range(0, 31, 5):
        s._check_offline(reader.frame.capture_t + dt)
    assert notifier.closes() == [] and engine.open_incident_count() == 1


# --------------------------------------------------------------------------- perf.csv, zone wording


def test_perf_csv_rows(tmp_path):
    summary = {"wall_s": 30.0, "busy_pct": 33.3, "gui_pct": 0.0, "cams": {1: {
        "processed": 148, "skipped": 1, "stale": 1, "errors": 0, "detect_ms": {"mean": 31.5, "p95": 35.0},
        "lag_ms": {"p50": 60.0, "p95": 80.0}}}}

    class R:
        fps = 25.0

    class N:
        jobs, posts = [1], []

    p = PerfCsv(tmp_path / "logs" / "perf.csv")
    p.write(summary, {1: R()}, {}, N())
    p.write(summary, {1: R()}, {}, N())
    rows = list(csv.DictReader((tmp_path / "logs" / "perf.csv").open()))
    assert len(rows) == 2                                   # one header only
    r = rows[0]
    assert r["cam1_in_fps"] == "25.0" and r["cam1_proc_fps"] == "4.93" and r["cam1_missed"] == "2"
    assert r["cam1_detect_p95_ms"] == "35.0" and r["cam1_lag_p95_ms"] == "80.0" and r["busy_pct"] == "33.3"
    assert float(r["rss_mb"]) > 10 and r["cpu_pct"] != "" and r["queue_db"] == "1" and r["open_incidents"] == "0"


def test_zone_reload_says_on_or_off():
    z = Zone(4, 2, "Outside", ZoneType.IGNORE, ((0, 0), (10, 0), (10, 10)), True)
    on = WorkerSettings({}, {4: z}, {}, {})
    off = WorkerSettings({}, {4: Zone(4, 2, "Outside", ZoneType.IGNORE, z.polygon, False)}, {}, {})
    assert diff(on, off).changed_zones[4] == "zone 4 'Outside' cam2: active: 1->0 (OFF)"
    assert diff(off, on).changed_zones[4] == "zone 4 'Outside' cam2: active: 0->1 (ON)"


def test_perf_csv_with_other_columns_is_kept_aside(tmp_path):
    path = tmp_path / "perf.csv"
    path.write_text("time,old_column\n2026-11-25T10:00:00+05:00,1\n")
    summary = {"wall_s": 30.0, "busy_pct": 10.0, "gui_pct": 0.0, "cams": {1: {
        "processed": 150, "skipped": 0, "stale": 0, "errors": 0, "detect_ms": {"mean": 30.0, "p95": 35.0},
        "lag_ms": {"p50": 50.0, "p95": 60.0}}}}

    class R:
        fps = 25.0

    PerfCsv(path).write(summary, {1: R()}, {}, None)
    kept = [f for f in tmp_path.iterdir() if f.name.startswith("perf.") and f.name != "perf.csv"]
    assert len(kept) == 1 and "old_column" in kept[0].read_text()
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 1 and rows[0]["cam1_processed"] == "150"
