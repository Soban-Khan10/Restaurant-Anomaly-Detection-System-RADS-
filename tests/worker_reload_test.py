"""POST /reload: RuleEngine.reload, validate/diff of new settings, and the scheduler applying a DB change."""
import logging
import sqlite3
import threading
import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from shared.contract import INTERNAL_HEADER, CameraStatus, TiedTo, ZoneType
from shared.db import create_schema, make_engine
from worker.linking import Worker
from worker.main import CameraPipeline, Reloader, Scheduler, build_engines, zone_object_classes
from worker.notifier import CloseJob
from worker.reader import Frame
from worker.reload import diff, validate
from worker.rules import IncidentClose, IncidentOpen, RuleEngine
from worker.settings import Camera, Hazard, Rule, WorkerSettings, Zone, load_settings
from worker.webserver import FrameHub, create_app

FPS, DT, T0 = 5.0, 0.2, 1_800_000_000.0
TOKEN = "reload-test-token"
HAZARDS = {
    "H-01": Hazard("H-01", "Head cover missing", "head_uncovered", "head_covered", TiedTo.WORKER),
    "H-04": Hazard("H-04", "Phone in preparation area", "phone", None, TiedTo.ZONE),
    "H-08": Hazard("H-08", "Mask missing", "mask_off", "mask_on", TiedTo.WORKER),
}
FULL = ((0.0, 0.0), (2688.0, 0.0), (2688.0, 1520.0), (0.0, 1520.0))


def rule(rid, hz, zone_ids=(1,), **kw):
    v = dict(enabled=True, conf_threshold=0.45, window_s=5.0, min_ratio=0.7, end_gap_s=5.0, cooldown_s=60.0,
             severity="Medium")
    v.update(kw)
    return Rule(rid, hz, 1, v["enabled"], v["conf_threshold"], v["window_s"], v["min_ratio"], v["end_gap_s"],
                v["cooldown_s"], v["severity"], tuple(zone_ids))


def worker(head="uncovered", mask="off"):
    w = Worker(1, (100.0, 100.0, 300.0, 700.0), head=head, mask=mask, confs={"person": 0.9}, zone_ids=(1,))
    w.confs.update({"head": 0.8, "mask": 0.8})
    return w


def phone(cx, zone):
    return ({"cls_name": "phone", "conf": 0.7, "xyxy": (cx - 20.0, 470.0, cx + 20.0, 530.0)}, (zone,))


def feed(engine, n, t, workers=(), objects=()):
    events = []
    for i in range(n):
        events += engine.update(t + i * DT, list(workers), list(objects))
    return events, t + n * DT


# --------------------------------------------------------------------------- RuleEngine.reload


def test_unchanged_rules_keep_open_incidents_and_a_disabled_rule_closes_its_own():
    rules = [rule(1, "H-01"), rule(2, "H-08")]
    e = RuleEngine(1, rules, HAZARDS, FPS)
    ev, t = feed(e, 20, T0, [worker()])
    assert {x.hazard_id for x in ev if isinstance(x, IncidentOpen)} == {"H-01", "H-08"}
    new = [rule(1, "H-01"), rule(2, "H-08", enabled=False)]
    closes = e.reload(new, HAZARDS, changed_rule_ids=[], close_rule_ids=[2], changed_zone_ids=[], t=t)
    assert [(c.hazard_id, c.reason) for c in closes] == [("H-08", "rule changed")]
    assert [k[0] for k in e.open_incidents()] == ["H-01"]                    # H-01 untouched
    ev, t = feed(e, 20, t, [worker()])
    assert ev == []                                                          # no new H-01, H-08 no longer checked


def test_value_change_keeps_the_open_incident_and_resets_the_window():
    e = RuleEngine(1, [rule(1, "H-01")], HAZARDS, FPS)
    ev, t = feed(e, 20, T0, [worker()])
    (o,) = [x for x in ev if isinstance(x, IncidentOpen)]
    closes = e.reload([rule(1, "H-01", window_s=8.0)], HAZARDS, changed_rule_ids=[1], close_rule_ids=[],
                      changed_zone_ids=[], t=t)
    assert closes == [] and e.rules[0].window_s == 8.0
    st = e._states[("H-01", ("track", 1))]
    assert st.incident.ref == o.ref and len(st.obs) == 0                     # incident kept, window reset
    ev, t = feed(e, 40, t, [worker(head="covered")])                         # closes under the new values
    assert [c.ref for c in ev if isinstance(c, IncidentClose)] == [o.ref]


def test_changed_zone_resets_only_its_own_state():
    h04 = rule(1, "H-04", zone_ids=(1, 5), window_s=10.0, min_ratio=0.3, end_gap_s=60.0, cooldown_s=300.0)
    e = RuleEngine(1, [h04], HAZARDS, FPS)
    ev, t = feed(e, 100, T0, objects=[phone(500, 1), phone(1500, 5)])
    opens = {x.zone_id: x.ref for x in ev if isinstance(x, IncidentOpen)}
    assert set(opens) == {1, 5}
    closes = e.reload([h04], HAZARDS, changed_rule_ids=[], close_rule_ids=[], changed_zone_ids=[5], t=t)
    assert [(c.ref, c.reason) for c in closes] == [(opens[5], "zone changed")]
    assert e.spots("H-04", 5) == [] and len(e.spots("H-04", 1)) == 1         # zone 1 spot kept
    assert [k[1][1] for k in e.open_incidents()] == [1]                      # zone 1 incident still open


# --------------------------------------------------------------------------- validate / diff


def settings(zones=None, rules=None, cameras=None, bad=()):
    return WorkerSettings(cameras or {1: Camera(1, "Camera 1", "rtsp://a", True, 5.0, None, None)},
                          zones or {1: Zone(1, 1, "Prep", ZoneType.PREP, FULL, True)}, HAZARDS,
                          rules or {1: rule(1, "H-01")}, tuple(bad))


def test_broken_polygon_keeps_the_old_one_and_logs_an_error(caplog):
    old = settings()
    new_json_broken = settings(zones={}, rules={1: rule(1, "H-01", window_s=8.0)}, bad=(1,))
    with caplog.at_level(logging.ERROR, logger="worker.reload"):
        merged, errors = validate(old, new_json_broken)
    assert merged.zones[1].polygon == FULL                                   # old polygon kept
    assert merged.rules[1].window_s == 8.0                                   # the good change still applied
    assert len(errors) == 1 and any(r.levelno == logging.ERROR and "zone 1" in r.message for r in caplog.records)

    two_points = settings(zones={1: Zone(1, 1, "Prep", ZoneType.PREP, ((0.0, 0.0), (10.0, 10.0)), True)})
    merged, errors = validate(old, two_points)
    assert merged.zones[1].polygon == FULL and len(errors) == 1


def test_bad_rule_value_keeps_the_old_rule():
    merged, errors = validate(settings(), settings(rules={1: rule(1, "H-01", min_ratio=1.7)}))
    assert merged.rules[1].min_ratio == 0.7 and "min_ratio" in errors[0]


def test_diff():
    old = settings(rules={1: rule(1, "H-01"), 2: rule(2, "H-08"), 3: rule(3, "H-04")},
                   zones={1: Zone(1, 1, "Prep", ZoneType.PREP, FULL, True),
                          5: Zone(5, 1, "Bin", ZoneType.WASTE, ((0, 0), (9, 0), (9, 9)), True)})
    new = settings(rules={1: rule(1, "H-01", window_s=8.0), 2: rule(2, "H-08", enabled=False),
                          3: rule(3, "H-04", zone_ids=(5,))},
                   zones={1: Zone(1, 1, "Prep", ZoneType.PREP, FULL, True),
                          5: Zone(5, 1, "Bin", ZoneType.WASTE, ((0, 0), (19, 0), (19, 19)), True)},
                   cameras={1: Camera(1, "Camera 1", "rtsp://b", True, 5.0, None, None)})
    d = diff(old, new)
    assert d.changed_rules == {1: ["H-01 cam1: window_s 5→8"]}
    assert set(d.closed_rules) == {2, 3} and "disabled" in d.closed_rules[2] and "zones" in d.closed_rules[3]
    assert list(d.changed_zones) == [5] and list(d.changed_cameras) == [1]
    assert diff(old, old).empty()


# --------------------------------------------------------------------------- scheduler + POST /reload


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
        self.head = False

    def detect(self, image):
        dets = [{"cls_name": "person", "conf": 0.9, "xyxy": (1000.0, 300.0, 1300.0, 1400.0)},
                {"cls_name": "mask_off", "conf": 0.8, "xyxy": (1110.0, 400.0, 1190.0, 450.0)}]
        if self.head:
            dets.append({"cls_name": "head_uncovered", "conf": 0.8, "xyxy": (1100.0, 310.0, 1200.0, 390.0)})
        return dets


class FakeNotifier:
    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rads.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (2, 'Camera 2', 'rtsp://x');
        INSERT INTO zones (zone_id, camera_id, name, type, polygon) VALUES
            (2, 2, 'Prep area 2', 'prep', '[[0,0],[2688,0],[2688,1520],[0,1520]]');
        INSERT INTO hazards VALUES ('H-01', 'Head cover missing', 'head_uncovered', 'head_covered', 'worker'),
                                   ('H-08', 'Mask missing', 'mask_off', 'mask_on', 'worker');
        INSERT INTO rules (rule_id, hazard_id, camera_id) VALUES (1, 'H-01', 2), (2, 'H-08', 2);
        INSERT INTO rule_zones VALUES (1, 2), (2, 2);
    """)
    con.commit()
    con.close()
    engine = make_engine(path)
    yield engine
    engine.dispose()


def test_reload_via_http_next_frame_uses_the_changed_rule(db):
    ws = load_settings(db)
    pipelines = {2: CameraPipeline(2, ws.zones_for(2), FPS, zone_object_classes(ws))}
    engines = build_engines(ws, pipelines, FPS)
    reader, detector, notifier, ev = FakeReader(), FakeDetector(), FakeNotifier(), threading.Event()
    sched = Scheduler({2: reader}, detector, pipelines, interval=DT, show=False, display_width=960,
                      report_every=30, duration=0, stop=threading.Event(), engines=engines, notifier=notifier,
                      settings=ws, reload_event=ev, reloader=Reloader(load=lambda: load_settings(db)))
    client = TestClient(create_app(FrameHub([2]), TOKEN, ev))
    base, seq = time.perf_counter() - 1000.0, [0]

    def step(n):
        for _ in range(n):
            sched._check_reload()                          # what the loop does before each frame
            seq[0] += 1
            reader.frame = Frame(np.zeros((1520, 2688, 3), np.uint8), base + seq[0] * DT, seq[0])
            sched._process(2, sched._window, sched._total)

    def open_hazards():
        return sorted(k[0] for k in engines[2].open_incidents())

    step(15)                                               # mask off from the start, head not seen yet
    assert open_hazards() == ["H-08"]
    with db.begin() as con:                                # the dashboard changes H-01's window
        con.exec_driver_sql("UPDATE rules SET window_s = 8 WHERE rule_id = 1")
    assert client.post("/reload").status_code == 401 and not ev.is_set()
    assert client.post("/reload", headers={INTERNAL_HEADER: TOKEN}).json() == {"ok": True}
    detector.head = True                                   # from now on the head is uncovered
    step(1)                                                # the next frame: reload applied first
    assert engines[2].rules[0].window_s == 8.0 and not ev.is_set()
    assert open_hazards() == ["H-08"]                      # unchanged H-08 kept its open incident ...
    assert not [j for j in notifier.jobs if isinstance(j, CloseJob)]   # ... and nothing was closed
    step(18)                                               # 19 head observations: the 5 s rule would have fired
    assert open_hazards() == ["H-08"]
    step(1)                                                # 20th = ceil(8 s x 5 FPS / 2)
    assert open_hazards() == ["H-01", "H-08"]

    with db.begin() as con:                                # disable H-08 -> its open incident closes (message 5)
        con.exec_driver_sql("UPDATE rules SET enabled = 0 WHERE rule_id = 2")
    client.post("/reload", headers={INTERNAL_HEADER: TOKEN})
    step(1)
    assert open_hazards() == ["H-01"]
    closes = [j.event for j in notifier.jobs if isinstance(j, CloseJob)]
    assert [(c.hazard_id, c.reason) for c in closes] == [("H-08", "rule changed")]


def test_reload_with_unreadable_db_keeps_the_settings(db, caplog):
    ws = load_settings(db)
    pipelines = {2: CameraPipeline(2, ws.zones_for(2), FPS, zone_object_classes(ws))}
    engines = build_engines(ws, pipelines, FPS)

    def broken():
        raise RuntimeError("database is locked")

    sched = Scheduler({2: FakeReader()}, FakeDetector(), pipelines, interval=DT, show=False, display_width=960,
                      report_every=30, duration=0, stop=threading.Event(), engines=engines, settings=ws,
                      reloader=Reloader(load=broken))
    sched._reload()
    assert sched.settings is ws and len(engines[2].rules) == 2
    assert any("keeping the current settings" in r.message for r in caplog.records)
