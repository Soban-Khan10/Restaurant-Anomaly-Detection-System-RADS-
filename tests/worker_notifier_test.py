"""Notifier: DB transaction + evidence JPEG, close, heartbeats, startup cleanup, API retries, queue policy."""
import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from shared.contract import (INTERNAL_HEADER, PATH_HEARTBEAT, PATH_INCIDENT_CLOSED, PATH_INCIDENT_NEW,
                             CameraStatus, Heartbeat, IncidentClosed, IncidentNew, ZoneType)
from shared.db import create_schema, make_engine
from worker.linking import Worker
from worker.notifier import CloseJob, HeartbeatJob, JobQueue, Notifier, OpenJob, iso_time
from worker.rules import IncidentClose, IncidentOpen
from worker.settings import Rule, Zone

TOKEN = "test-token"
T0 = 1_800_000_000.0
RULE_H01 = Rule(1, "H-01", 1, True, 0.45, 5.0, 0.7, 5.0, 60.0, "High", (1,))
RULE_H04 = Rule(2, "H-04", 1, True, 0.45, 10.0, 0.3, 60.0, 300.0, "Medium", (1,))
ZONE = Zone(1, 1, "Prep area 1", ZoneType.PREP, ((0, 0), (2688, 0), (2688, 1520), (0, 1520)), True)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rads.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (1, 'Camera 1', 'rtsp://x'), (2, 'Camera 2', 'rtsp://y');
        INSERT INTO zones (zone_id, camera_id, name, type, polygon) VALUES (1, 1, 'Prep area 1', 'prep', '[]');
        INSERT INTO hazards VALUES ('H-01', 'Head cover missing', 'head_uncovered', 'head_covered', 'worker'),
                                   ('H-04', 'Phone in preparation area', 'phone', NULL, 'zone');
        INSERT INTO rules (rule_id, hazard_id, camera_id, severity) VALUES (1, 'H-01', 1, 'High'), (2, 'H-04', 1, 'Medium');
    """)
    con.commit()
    con.close()
    engine = make_engine(path)
    yield engine
    engine.dispose()


class FakeApi:
    """httpx transport that records requests; `fail` = number of calls that raise ConnectError first."""

    def __init__(self, fail: int = 0, status: int = 200):
        self.requests: list[httpx.Request] = []
        self.fail = fail
        self.status = status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            self.fail -= 1
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(self.status, json={"ok": True})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]


def notifier(db, tmp_path, api: FakeApi, **kw) -> Notifier:
    kw.setdefault("backoff_s", 0.01)
    return Notifier(evidence_dir=tmp_path / "evidence", api_url="http://127.0.0.1:8000", token=TOKEN,
                    model_version="v2_960_best", db_engine=db, http_client=api.client(), **kw)


def open_job(ref=1, rule=RULE_H01, track_id=7, spot_id=None):
    trigger = {"cls_name": "head_uncovered", "conf": 0.81, "xyxy": (1200.0, 300.0, 1300.0, 420.0)}
    w = Worker(track_id or 1, (1100.0, 250.0, 1450.0, 1500.0), head="uncovered", confs={"person": 0.9, "head": 0.81},
               zone_ids=(1,))
    ev = IncidentOpen(ref, 1, rule, rule.hazard_id, 1, track_id, spot_id, T0, T0 + 2.4, 0.7, 0.81, trigger,
                      w.box if track_id else None)
    frame = np.full((1520, 2688, 3), 90, np.uint8)
    return OpenJob(ev, frame, T0 + 2.4, [trigger], [w] if track_id else [], [ZONE], "Head cover missing")


def _wait(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    assert cond()


def rows(db, sql):
    with db.connect() as con:
        return [dict(r._mapping) for r in con.exec_driver_sql(sql)]


def test_open_writes_incident_evidence_and_jpeg_in_one_go_then_message_2(db, tmp_path):
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job())
    assert n.stop(10)

    (inc,) = rows(db, "SELECT * FROM incidents")
    assert inc["hazard_id"] == "H-01" and inc["rule_id"] == 1 and inc["camera_id"] == 1 and inc["zone_id"] == 1
    assert inc["track_id"] == 7 and inc["status"] == "New" and inc["severity"] == "High"
    assert inc["model_version"] == "v2_960_best" and inc["end_time"] is None
    assert inc["start_time"] == iso_time(T0) and inc["start_time"].endswith("+05:00")
    (ev,) = rows(db, "SELECT * FROM evidence")
    day = datetime.fromisoformat(iso_time(T0 + 2.4)).strftime("%Y-%m-%d")
    assert ev["incident_id"] == inc["incident_id"] and ev["kind"] == "snapshot"
    assert ev["file_path"] == f"{day}/cam1/{inc['incident_id']}.jpg"
    det = json.loads(ev["detections"])
    assert det["trigger"]["cls_name"] == "head_uncovered" and det["workers"][0]["head"] == "uncovered"
    img = cv2.imread(str(tmp_path / "evidence" / ev["file_path"]))
    assert img is not None and img.shape == (1520, 2688, 3)

    (req,) = api.requests
    assert req.url.path == PATH_INCIDENT_NEW and req.headers[INTERNAL_HEADER] == TOKEN
    assert IncidentNew.model_validate_json(req.content).incident_id == inc["incident_id"]
    assert json.loads(req.content) == {"incident_id": inc["incident_id"]}


def test_jpeg_write_failure_writes_no_rows(db, tmp_path, monkeypatch):
    monkeypatch.setattr("worker.notifier.cv2.imwrite", lambda *a, **k: False)
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job())
    n.submit(CloseJob(IncidentClose(1, 1, "H-01", T0 + 9, 0.7, 0.81, "compliant")))
    assert n.stop(10)
    assert rows(db, "SELECT * FROM incidents") == [] and rows(db, "SELECT * FROM evidence") == []
    assert list((tmp_path / "evidence").rglob("*.jpg")) == []
    assert api.requests == []                                    # no message 2 or 5 for a rolled-back incident
    assert n.stats["open_failed"] == 1


def test_close_updates_only_end_time_and_confs_then_message_5(db, tmp_path):
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job(ref=5))
    n.submit(CloseJob(IncidentClose(5, 1, "H-01", T0 + 30.0, 0.66, 0.93, "compliant")))
    assert n.stop(10)
    (inc,) = rows(db, "SELECT * FROM incidents")
    assert inc["end_time"] == iso_time(T0 + 30.0)
    assert (inc["conf_avg"], inc["conf_max"]) == (0.66, 0.93)
    assert inc["status"] == "New" and inc["start_time"] == iso_time(T0) and inc["reviewed_by"] is None
    assert [r.url.path for r in api.requests] == [PATH_INCIDENT_NEW, PATH_INCIDENT_CLOSED]
    assert IncidentClosed.model_validate_json(api.requests[1].content).incident_id == inc["incident_id"]


def test_h04_incident_has_no_track_id(db, tmp_path):
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job(rule=RULE_H04, track_id=None, spot_id=3))
    assert n.stop(10)
    (inc,) = rows(db, "SELECT * FROM incidents")
    assert inc["hazard_id"] == "H-04" and inc["track_id"] is None and inc["zone_id"] == 1


def test_heartbeat_upserts_camera_health_and_sends_message_6(db, tmp_path):
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(HeartbeatJob(1, CameraStatus.ONLINE, 4.96, T0, T0 - 0.1))
    _wait(lambda: n.stats["heartbeats"] == 1)
    n.submit(HeartbeatJob(1, CameraStatus.RECONNECTING, 0.0, T0 + 5, None))
    assert n.stop(10)
    (h,) = rows(db, "SELECT * FROM camera_health")
    assert h["status"] == "reconnecting" and h["fps"] == 0.0
    assert h["last_seen"] == iso_time(T0 - 0.1)                  # no new frame: last_seen kept
    bodies = api.bodies(PATH_HEARTBEAT)
    assert len(bodies) >= 1
    for b in bodies:
        hb = Heartbeat.model_validate(b)                         # extra="forbid": exactly the contract fields
        assert hb.camera_id == 1 and hb.ts.endswith("+05:00")


def test_api_down_retries_then_continues(db, tmp_path):
    api = FakeApi(fail=10**6)                                    # API down for good
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job(ref=1))
    n.submit(CloseJob(IncidentClose(1, 1, "H-01", T0 + 30.0, 0.7, 0.8, "compliant")))
    n.submit(open_job(ref=2, track_id=8))
    assert n.stop(20)
    assert len(rows(db, "SELECT * FROM incidents")) == 2        # the DB writes went on
    assert rows(db, "SELECT end_time FROM incidents ORDER BY incident_id")[0]["end_time"] == iso_time(T0 + 30)
    paths = [r.url.path for r in api.requests]
    assert paths.count(PATH_INCIDENT_NEW) == 2 * 4               # 1 try + 3 retries each
    assert paths.count(PATH_INCIDENT_CLOSED) == 4
    assert n.stats["gave_up"] == 3 and n.stats["retries"] == 9


def test_api_back_after_two_failures_message_still_arrives(db, tmp_path):
    api = FakeApi(fail=2)
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job())
    assert n.stop(10)
    assert [r.url.path for r in api.requests] == [PATH_INCIDENT_NEW] * 3
    assert n.stats["sent"] == 1 and n.stats["gave_up"] == 0


def test_rejected_message_is_not_retried(db, tmp_path):
    api = FakeApi(status=401)
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(open_job())
    assert n.stop(10)
    assert len(api.requests) == 1 and n.stats["rejected"] == 1


def test_startup_closes_incidents_left_open_by_previous_run(db, tmp_path):
    with db.begin() as con:
        con.exec_driver_sql("INSERT INTO incidents (hazard_id, rule_id, camera_id, zone_id, start_time, severity, "
                            "model_version) VALUES ('H-01', 1, 1, 1, '2026-11-24T10:00:00+05:00', 'High', 'v2')")
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start()
    assert n.stop(10)
    (inc,) = rows(db, "SELECT * FROM incidents")
    assert inc["end_time"] == inc["start_time"] == "2026-11-24T10:00:00+05:00"
    assert api.bodies(PATH_INCIDENT_CLOSED) == [{"incident_id": inc["incident_id"]}]


def test_queue_full_drops_heartbeats_first_never_events():
    q = JobQueue(3, "test")
    assert q.put("hb1", droppable=True, coalesce_key=("hb", 1))
    assert q.put("hb1b", droppable=True, coalesce_key=("hb", 1))   # replaces the waiting heartbeat of cam 1
    assert q.put("hb2", droppable=True, coalesce_key=("hb", 2))
    assert q.put("open1") and len(q) == 3
    assert q.put("open2") and len(q) == 3                           # evicted a heartbeat to make room
    assert not q.put("hb3", droppable=True, coalesce_key=("hb", 3))  # full: a new heartbeat is dropped
    assert q.put("close1") and len(q) == 3                          # the last heartbeat gives way
    assert q.put("close2") and len(q) == 4                          # no heartbeat left: events are never dropped
    assert q.over_bound == 1 and q.dropped == 4
    assert [q.get(0.01) for _ in range(4)] == ["open1", "open2", "close1", "close2"]


def test_submit_never_blocks_when_notifier_is_stalled(db, tmp_path):
    n = notifier(db, tmp_path, FakeApi(), maxsize=5)               # not started: nothing is consumed
    for i in range(50):
        n.submit(HeartbeatJob(1 + i % 2, CameraStatus.ONLINE, 5.0, T0 + i, T0 + i))
    for i in range(10):
        assert n.submit(CloseJob(IncidentClose(i, 1, "H-01", T0, 0.5, 0.5, "x")))
    assert len(n.jobs) == 10                                     # every event kept, heartbeats gave way


# --------------------------------------------------------------------------- with the real mock_api app


def _mock_api_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mock_api.py"
    spec = importlib.util.spec_from_file_location("rads_mock_api", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_notifier_against_mock_api_end_to_end(db, tmp_path):
    from fastapi.testclient import TestClient

    app = _mock_api_module().create_app(token=TOKEN)
    client = TestClient(app)
    n = Notifier(evidence_dir=tmp_path / "evidence", api_url="http://testserver", token=TOKEN,
                 model_version="v2_960_best", db_engine=db, http_client=client, backoff_s=0.01)
    n.start(close_stale=False)
    n.submit(open_job(ref=1))
    n.submit(CloseJob(IncidentClose(1, 1, "H-01", T0 + 9, 0.7, 0.8, "compliant")))
    n.submit(HeartbeatJob(1, CameraStatus.ONLINE, 5.0, T0, T0))
    assert n.stop(10)
    calls = client.get("/debug/calls").json()
    assert [c["path"] for c in calls] == [PATH_INCIDENT_NEW, PATH_INCIDENT_CLOSED, PATH_HEARTBEAT]
    assert n.stats["sent"] == 3 and n.stats["rejected"] == 0


def test_heartbeats_are_not_retried(db, tmp_path):
    api = FakeApi(fail=10**6)
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(HeartbeatJob(1, CameraStatus.ONLINE, 5.0, T0, T0))
    _wait(lambda: n.stats["heartbeats"] == 1)
    n.submit(HeartbeatJob(2, CameraStatus.ONLINE, 5.0, T0, T0))
    assert n.stop(10)
    assert [r.url.path for r in api.requests] == [PATH_HEARTBEAT, PATH_HEARTBEAT]   # one attempt each
    assert n.stats["heartbeats_failed"] == 2 and n.stats["retries"] == 0
    assert len(rows(db, "SELECT * FROM camera_health")) == 2                         # the DB rows still written


def test_priority_queue_gives_messages_before_heartbeats():
    q = JobQueue(10, "http", priority=True)
    q.put("hb1", droppable=True, coalesce_key=("hb", 1))
    q.put("new 7")
    q.put("hb2", droppable=True, coalesce_key=("hb", 2))
    q.put("closed 7")
    assert [q.get(0.01) for _ in range(4)] == ["new 7", "closed 7", "hb1", "hb2"]


def test_waiting_messages_2_and_5_are_sent_before_waiting_heartbeats(db, tmp_path):
    import threading

    release = threading.Event()

    class SlowFirstApi(FakeApi):
        def handler(self, request):
            if not self.requests:
                release.wait(5)            # the first request (a heartbeat) hangs until released
            return super().handler(request)

    api = SlowFirstApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    n.submit(HeartbeatJob(1, CameraStatus.ONLINE, 5.0, T0, T0))
    _wait(lambda: len(api.requests) == 0 and n.stats["heartbeats"] == 1)
    n.submit(HeartbeatJob(2, CameraStatus.ONLINE, 5.0, T0, T0))   # waits behind the hanging request ...
    n.submit(open_job(ref=1))                                     # ... and so do these two
    n.submit(CloseJob(IncidentClose(1, 1, "H-01", T0 + 9, 0.7, 0.8, "compliant")))
    _wait(lambda: n.stats["closed"] == 1 and len(n.posts) == 3)
    release.set()
    assert n.stop(10)
    paths = [r.url.path for r in api.requests]
    assert paths == [PATH_HEARTBEAT, PATH_INCIDENT_NEW, PATH_INCIDENT_CLOSED, PATH_HEARTBEAT]


def test_heartbeat_for_a_camera_not_in_the_db_warns_once_and_still_sends(db, tmp_path, caplog):
    api = FakeApi()
    n = notifier(db, tmp_path, api)
    n.start(close_stale=False)
    for i in range(3):
        n.submit(HeartbeatJob(9, CameraStatus.ONLINE, 5.0, T0 + 5 * i, T0 + 5 * i))
        _wait(lambda: len(api.requests) == i + 1)
    assert n.stop(10)
    assert rows(db, "SELECT * FROM camera_health") == []
    assert n.stats["db_errors"] == 0
    assert sum("camera 9 is not in the cameras table" in r.message for r in caplog.records) == 1
