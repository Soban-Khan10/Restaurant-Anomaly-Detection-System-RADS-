import time

import cv2
import numpy as np
import pytest

from shared.contract import CameraStatus
from worker.reader import CameraReader, redact

FPS = 10
N_FRAMES = 20


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """A tiny 64x48, 10 FPS, 20-frame (2 s) video."""
    path = tmp_path_factory.mktemp("clip") / "tiny.mp4"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (64, 48))
    assert vw.isOpened()
    for i in range(N_FRAMES):
        vw.write(np.full((48, 64, 3), i * 10, dtype=np.uint8))
    vw.release()
    return path


def _wait(cond, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_file_reader_online_newest_frame_and_rate(clip):
    r = CameraReader(1, clip, retry_s=0.2)
    assert r.is_file
    r.start()
    try:
        assert r.first_frame.wait(5)
        assert r.status == CameraStatus.ONLINE
        assert (r.width, r.height) == (64, 48)
        f1 = r.latest()
        time.sleep(0.5)
        f2 = r.latest()
        assert f2.seq > f1.seq                      # slot is overwritten with newer frames
        assert f2.capture_t > f1.capture_t
        assert f2.image.shape == (48, 64, 3)
        assert r.source_fps == pytest.approx(FPS, abs=0.5)
        assert _wait(lambda: r.frames >= 12, 3)
        assert r.fps == pytest.approx(FPS, rel=0.25)  # paced at the file's real rate, not as fast as possible
    finally:
        r.stop(timeout=3)
    assert not r.is_alive()
    assert r.status == CameraStatus.OFFLINE


def test_file_reader_loops_at_end(clip):
    r = CameraReader(1, clip, retry_s=0.2)
    r.start()
    try:
        assert _wait(lambda: r.loops >= 1 and r.frames > N_FRAMES, 5)  # 2 s clip
        assert r.latest().seq > N_FRAMES  # seq keeps counting across loops
        assert r.status == CameraStatus.ONLINE
        assert r.reconnects == 0
    finally:
        r.stop(timeout=3)


def test_bad_source_reconnects_without_crashing(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_bytes(b"this is not a video")
    for src in (bad, "rtsp://user:secret@127.0.0.1:1/none"):
        r = CameraReader(2, src, retry_s=0.2)
        r.start()
        try:
            assert _wait(lambda: r.reconnects >= 2, 15)
            assert r.is_alive()
            assert r.status == CameraStatus.RECONNECTING
            assert r.latest() is None
        finally:
            r.stop(timeout=7)
        assert not r.is_alive()


def test_stop_is_quick_during_retry_wait(tmp_path):
    r = CameraReader(1, tmp_path / "missing.mp4", retry_s=60)
    r.start()
    assert _wait(lambda: r.status == CameraStatus.RECONNECTING, 5)
    t = time.monotonic()
    r.stop(timeout=3)
    assert not r.is_alive()
    assert time.monotonic() - t < 1.0


def test_redact_hides_credentials():
    assert redact("rtsp://admin:pw@192.168.1.64:554/x") == "rtsp://***@192.168.1.64:554/x"
    assert redact("/path/to/clip.mp4") == "/path/to/clip.mp4"
