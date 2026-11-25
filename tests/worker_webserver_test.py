"""Worker web server: /stream (MJPEG, annotated and ?raw=1), /reload (token), /health, and the frame hand-off."""
import logging
import re
import socket
import threading
import time

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from shared.contract import INTERNAL_HEADER, ZoneType
from worker.linking import Worker
from worker.main import FrameResult
from worker.settings import Zone
from worker.webserver import ENCODE_THREADS, STREAM_WIDTH, FrameHub, WebServer, create_app

TOKEN = "worker-test-token"
ZONES = [Zone(1, 1, "Prep area 1", ZoneType.PREP, ((0, 0), (2688, 0), (2688, 1520), (0, 1520)), True)]


def frame_and_result(i=0):
    img = np.full((1520, 2688, 3), 60 + (i % 100), np.uint8)
    w = Worker(3, (1000.0, 300.0, 1300.0, 1400.0), head="uncovered", confs={"person": 0.9, "head": 0.8},
               zone_ids=(1,))
    dets = [{"cls_name": "person", "conf": 0.9, "xyxy": w.box},
            {"cls_name": "head_uncovered", "conf": 0.8, "xyxy": (1100.0, 320.0, 1200.0, 420.0)}]
    return img, FrameResult(1, 0.0, (1520, 2688), dets, [w], [], [], [], [], [])


def publish(hub, cam, i=0):
    img, res = frame_and_result(i)
    hub.publish(cam, img, res, ZONES, [f"cam{cam} test frame {i}"])


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.02)
    return cond()


def decode(part):
    return cv2.imdecode(np.frombuffer(part, np.uint8), cv2.IMREAD_COLOR)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_parts(url, n, timeout=10.0):
    """Read n JPEG parts from an MJPEG stream."""
    parts, buf = [], b""
    with httpx.stream("GET", url, timeout=timeout) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        for chunk in r.iter_bytes():
            buf += chunk
            while True:
                i = buf.find(b"\r\n\r\n")
                m = re.search(rb"Content-Length: (\d+)", buf[:i]) if i >= 0 else None
                if not m or len(buf) < i + 4 + int(m.group(1)):
                    break
                length = int(m.group(1))
                parts.append(buf[i + 4:i + 4 + length])
                buf = buf[i + 4 + length + 2:]
            if len(parts) >= n:
                return parts
    return parts


def start_publisher(hub, cam=1):
    """Like the main loop: a new frame for `cam` every 0.2 s. Returns the stop event."""
    stop = threading.Event()

    def run():
        i = 0
        while not stop.is_set():
            publish(hub, cam, i)
            i += 1
            stop.wait(0.2)

    threading.Thread(target=run, name="test-publisher", daemon=True).start()
    return stop


@pytest.fixture
def server():
    hub = FrameHub([1, 2])
    web = WebServer(hub, TOKEN, port=free_port())
    assert web.start()
    stop = start_publisher(hub)
    yield hub, f"http://127.0.0.1:{web.port}"
    stop.set()
    web.stop()


def test_stream_sends_multipart_jpeg_frames(server):
    hub, base = server
    parts = read_parts(f"{base}/stream/1", 3)
    assert len(parts) == 3
    for p in parts:
        img = decode(p)
        assert img is not None and img.shape[:2] == (760, STREAM_WIDTH)   # 1344 x 760


def test_first_frame_arrives_within_half_a_second(server):
    hub, base = server
    assert wait_for(lambda: hub.seq(1) > 0)
    for path in ("/stream/1", "/stream/1?raw=1", "/stream/1", "/stream/1?raw=1"):   # nothing cached, then cached
        t = time.monotonic()
        parts = read_parts(base + path, 1)
        took = time.monotonic() - t
        assert len(parts) == 1 and took < 0.5, (path, took)


def test_stream_is_about_2_fps(server):
    hub, base = server
    t = time.monotonic()
    read_parts(f"{base}/stream/1", 5)
    assert time.monotonic() - t >= 1.5                                # 5 frames take >= 4 x 0.5 s


def test_two_viewers_at_once(server):
    hub, base = server
    got, seen_viewers = {}, []

    def view(k):
        got[k] = read_parts(f"{base}/stream/1", 3)

    threads = [threading.Thread(target=view, args=(k,)) for k in (0, 1)]
    for t in threads:
        t.start()
    for _ in range(100):
        seen_viewers.append(hub.viewer_count(1))
        time.sleep(0.02)
    for t in threads:
        t.join(15)
    assert [len(got[k]) for k in (0, 1)] == [3, 3]
    assert max(seen_viewers) == 2


def test_unknown_camera_is_404(server):
    hub, base = server
    assert httpx.get(f"{base}/stream/9").status_code == 404


def test_camera_without_frame_keeps_the_connection_open(server):
    hub, base = server
    got = []
    t = threading.Thread(target=lambda: got.extend(read_parts(f"{base}/stream/2", 1)))
    t.start()
    time.sleep(0.8)
    assert got == [] and hub.viewer_count(2) == 1                     # waiting, connection open
    publish(hub, 2)
    t.join(10)
    assert len(got) == 1


def test_no_viewers_means_no_encoding():
    hub = FrameHub([1])
    for i in range(50):
        publish(hub, 1, i)
    assert hub.encodes == {"annotated": 0, "raw": 0}                  # publish is references only
    hub._encode(1)
    hub._encode(1)
    assert hub.encodes == {"annotated": 1, "raw": 0}                  # cached per frame
    hub._encode(1, raw=True)
    hub._encode(1, raw=True)
    assert hub.encodes == {"annotated": 1, "raw": 1}                  # raw has its own cache


def test_viewer_starts_encoding(server):
    hub, base = server
    time.sleep(0.5)
    assert hub.encodes == {"annotated": 0, "raw": 0}
    read_parts(f"{base}/stream/1", 2)
    assert hub.encodes["annotated"] >= 2


def test_raw_and_annotated_are_encoded_separately_and_only_while_watched(server):
    hub, base = server
    time.sleep(0.5)
    read_parts(f"{base}/stream/1?raw=1", 2)
    assert wait_for(lambda: hub.viewer_count() == 0)
    assert hub.encodes["raw"] >= 2 and hub.encodes["annotated"] == 0     # only raw was watched
    n_raw = hub.encodes["raw"]
    read_parts(f"{base}/stream/1", 2)
    assert wait_for(lambda: hub.viewer_count() == 0)
    assert hub.encodes["annotated"] >= 2 and hub.encodes["raw"] == n_raw  # only annotated was watched
    n = dict(hub.encodes)
    time.sleep(0.7)                                                       # frames keep coming, nobody watches
    assert hub.encodes == n


def test_raw_frames_have_no_drawings_and_the_same_size(server):
    hub, base = server
    publish(hub, 2, 0)                                   # one fixed frame on camera 2: plain grey (60)
    raw = decode(read_parts(f"{base}/stream/2?raw=1", 1)[0])
    ann = decode(read_parts(f"{base}/stream/2", 1)[0])
    assert raw.shape == ann.shape == (760, STREAM_WIDTH, 3)
    assert np.abs(raw.astype(int) - 60).max() <= 3       # no box, zone, label or overlay
    changed = np.abs(ann.astype(int) - raw.astype(int)).max(axis=2) > 10
    assert changed.mean() > 0.01                         # the annotated one has drawings


def test_disabled_camera_is_404():
    hub = FrameHub([1, 2], disabled=[2])
    publish(hub, 2)
    c = TestClient(create_app(hub, TOKEN, threading.Event()))
    for path in ("/stream/2", "/stream/2?raw=1", "/stream/9", "/stream/9?raw=1"):
        assert c.get(path).status_code == 404, path
    assert hub.can_stream(1) and not hub.can_stream(2)
    hub.set_disabled([])
    assert hub.can_stream(2)


def test_disabling_a_camera_ends_its_open_stream(server):
    hub, base = server
    got = []
    t = threading.Thread(target=lambda: got.extend(read_parts(f"{base}/stream/1", 100)))
    t.start()
    time.sleep(1.2)
    hub.set_disabled([1])                                 # e.g. a reload after the dashboard disabled it
    t.join(5)
    assert not t.is_alive() and 1 <= len(got) < 100
    assert httpx.get(f"{base}/stream/1").status_code == 404
    assert wait_for(lambda: hub.viewer_count() == 0)


def test_quick_snapshots_leave_no_errors_and_no_threads(caplog, monkeypatch):
    """50 x connect, read one frame, disconnect (what a snapshot does): no errors, nothing left running."""
    thread_errors = []
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)
    caplog.set_level(logging.DEBUG)
    before = threading.active_count()
    hub = FrameHub([1, 2])
    web = WebServer(hub, TOKEN, port=free_port())
    assert web.start()
    stop = start_publisher(hub)
    base = f"http://127.0.0.1:{web.port}"
    try:
        for i in range(50):
            assert len(read_parts(f"{base}/stream/1" + ("?raw=1" if i % 2 else ""), 1)) == 1
        assert wait_for(lambda: hub.viewer_count() == 0)                 # every viewer counted out
        encoders = [t for t in threading.enumerate() if t.name.startswith("stream-encode")]
        assert len(encoders) <= ENCODE_THREADS                           # a fixed pool, not one per viewer
        assert threading.active_count() <= before + 2 + ENCODE_THREADS  # + web server + test publisher
        n = dict(hub.encodes)
        time.sleep(0.7)
        assert hub.encodes == n                                          # nobody watching: no encoding
    finally:
        stop.set()
        web.stop()
    assert wait_for(lambda: threading.active_count() <= before)          # back to normal
    assert not [t for t in threading.enumerate() if t.name.startswith(("stream-encode", "webserver"))]
    left = [r for r in caplog.records if "viewer left" in r.getMessage()]
    assert len(left) == 50 and {r.levelno for r in left} == {logging.DEBUG}
    bad = [r for r in caplog.records
           if r.exc_info or r.levelno >= logging.WARNING
           or (r.levelno >= logging.INFO and r.name.split(".")[0] in ("worker", "uvicorn", "asyncio", "starlette")
               and not r.getMessage().startswith("web server on"))]
    assert bad == [] and thread_errors == []


def test_reload_needs_the_token_and_only_sets_a_flag():
    ev = threading.Event()
    c = TestClient(create_app(FrameHub([1]), TOKEN, ev))
    assert c.post("/reload").status_code == 401
    assert c.post("/reload", headers={INTERNAL_HEADER: "wrong"}).status_code == 401
    assert not ev.is_set()
    r = c.post("/reload", headers={INTERNAL_HEADER: TOKEN})
    assert r.status_code == 200 and r.json() == {"ok": True} and ev.is_set()


def test_health_fields():
    hub = FrameHub([1, 2])
    hub.set_health({"crossing_check": False, "cameras": {
        1: {"status": "online", "input_fps": 25.0, "processed_fps": 5.0, "last_frame": "2026-11-25T10:00:00+05:00",
            "open_incidents": 2},
        2: {"status": "reconnecting", "input_fps": 0.0, "processed_fps": 0.0, "last_frame": None,
            "open_incidents": 0}}})
    h = TestClient(create_app(hub, TOKEN, threading.Event())).get("/health").json()
    assert h["crossing_check"] is False and h["open_incidents"] == 2 and h["time"].endswith("+05:00")
    c1 = h["cameras"]["1"]
    assert set(c1) == {"status", "input_fps", "processed_fps", "last_frame", "open_incidents", "viewers",
                       "raw_viewers"}
    assert c1["status"] == "online" and c1["processed_fps"] == 5.0 and c1["viewers"] == c1["raw_viewers"] == 0
    assert h["cameras"]["2"]["status"] == "reconnecting"


def test_web_server_listens_on_localhost_only():
    with pytest.raises(ValueError):
        WebServer(FrameHub([1]), TOKEN, host="0.0.0.0")


def test_busy_port_does_not_crash(caplog):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        web = WebServer(FrameHub([1]), TOKEN, port=s.getsockname()[1])
        assert web.start(timeout=3) is False
        web.stop()
    assert any("could not start" in r.message or "did not start" in r.message for r in caplog.records)
