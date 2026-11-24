"""Worker web server: /stream (MJPEG), /reload (token), /health, and the frame hand-off (FrameHub)."""
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
from worker.webserver import STREAM_WIDTH, FrameHub, WebServer, create_app

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


@pytest.fixture
def server():
    hub = FrameHub([1, 2])
    web = WebServer(hub, TOKEN, port=free_port())
    assert web.start()
    stop = threading.Event()

    def publisher():  # like the main loop: a new frame for camera 1 every 0.2 s
        i = 0
        while not stop.is_set():
            publish(hub, 1, i)
            i += 1
            stop.wait(0.2)

    t = threading.Thread(target=publisher, daemon=True)
    t.start()
    yield hub, f"http://127.0.0.1:{web.port}"
    stop.set()
    web.stop()


def test_stream_sends_multipart_jpeg_frames(server):
    hub, base = server
    parts = read_parts(f"{base}/stream/1", 3)
    assert len(parts) == 3
    for p in parts:
        img = cv2.imdecode(np.frombuffer(p, np.uint8), cv2.IMREAD_COLOR)
        assert img is not None and img.shape[1] == STREAM_WIDTH        # 1344 px wide


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
    assert hub.encodes == 0                                           # publish is references only
    hub._encode(1)
    hub._encode(1)
    assert hub.encodes == 1                                           # cached per frame


def test_viewer_starts_encoding(server):
    hub, base = server
    time.sleep(0.5)
    assert hub.encodes == 0
    read_parts(f"{base}/stream/1", 2)
    assert hub.encodes >= 2


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
    assert set(c1) == {"status", "input_fps", "processed_fps", "last_frame", "open_incidents", "viewers"}
    assert c1["status"] == "online" and c1["processed_fps"] == 5.0 and c1["viewers"] == 0
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
