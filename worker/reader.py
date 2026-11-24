"""CameraReader: one thread per camera that keeps only the newest frame.

The source is an RTSP URL or a video file path. A file is replayed at its real frame rate and looped at the end,
which is how the worker is tested without cameras. On any failure the reader goes to `reconnecting`, waits
`retry_s` (10 s) and tries again; it never raises out of the thread.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from shared.contract import CameraStatus

log = logging.getLogger(__name__)

DEFAULT_FILE_FPS = 25.0
MAX_BEHIND_S = 1.0  # a file reader further behind real time than this re-syncs its clock
RTSP_OPEN_TIMEOUT_MS = 10_000
RTSP_READ_TIMEOUT_MS = 5_000


def redact(source: str) -> str:
    """Hide credentials in an RTSP URL before it is logged: rtsp://user:pass@host -> rtsp://***@host."""
    return re.sub(r"//[^/@]+@", "//***@", source)


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    capture_t: float  # time.perf_counter() of capture (nominal frame time for a file)
    seq: int          # increases by one for every new frame from this reader


class CameraReader(threading.Thread):
    def __init__(self, camera_id: int, source: str | os.PathLike, *, retry_s: float = 10.0,
                 max_read_failures: int = 25) -> None:
        super().__init__(daemon=True, name=f"reader-cam{camera_id}")
        self.camera_id = camera_id
        src = str(source)
        path = Path(src).expanduser()
        self.is_file = path.is_file()
        self.source = str(path) if self.is_file else src
        self.retry_s = retry_s
        self.max_read_failures = max_read_failures

        self.status: CameraStatus = CameraStatus.OFFLINE
        self.frames = 0          # frames read since start
        self.loops = 0           # times a file was replayed from the start
        self.dropped = 0         # file frames skipped because the reader was behind real time
        self.reconnects = 0      # times the reader went to `reconnecting`
        self.width: int | None = None
        self.height: int | None = None
        self.source_fps: float | None = None
        self.first_frame = threading.Event()

        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._seq = 0
        self._times: deque[float] = deque(maxlen=50)
        self._stop_event = threading.Event()
        self._cap: cv2.VideoCapture | None = None

    # ------------------------------------------------------------------ public

    def latest(self) -> Frame | None:
        """The newest frame (or None before the first one). The caller must not modify `image` in place."""
        with self._lock:
            return self._latest

    @property
    def fps(self) -> float:
        """Published frames per second over the last ~50 frames (0 if none recently). Dropped frames don't count."""
        with self._lock:
            t = list(self._times)
        if len(t) < 2 or time.perf_counter() - t[-1] > 2.0:
            return 0.0
        return (len(t) - 1) / max(t[-1] - t[0], 1e-9)

    def stop(self, timeout: float | None = None) -> None:
        self._stop_event.set()
        if timeout is not None and self.is_alive():
            self.join(timeout)

    # ------------------------------------------------------------------ thread

    def run(self) -> None:
        log.info("cam%d: reader started (%s) %s", self.camera_id, "file" if self.is_file else "stream",
                 redact(self.source))
        while not self._stop_event.is_set():
            try:
                if self._open():
                    if self.is_file:
                        self._read_file()
                    else:
                        self._read_stream()
            except Exception:  # never let the thread die
                log.exception("cam%d: reader error", self.camera_id)
            finally:
                self._release()
            if self._stop_event.is_set():
                break
            self.status = CameraStatus.RECONNECTING
            self.reconnects += 1
            log.warning("cam%d: reconnecting in %.0f s", self.camera_id, self.retry_s)
            self._stop_event.wait(self.retry_s)
        self.status = CameraStatus.OFFLINE
        log.info("cam%d: reader stopped (%d frames, %d dropped, %d loops, %d reconnects)",
                 self.camera_id, self.frames, self.dropped, self.loops, self.reconnects)

    def _open(self) -> bool:
        if self.is_file:
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        else:
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_OPEN_TIMEOUT_MS,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, RTSP_READ_TIMEOUT_MS])
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            log.warning("cam%d: cannot open %s", self.camera_id, redact(self.source))
            return False
        self._cap = cap
        return True

    def _release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _publish(self, image: np.ndarray, capture_t: float) -> None:
        with self._lock:
            self._seq += 1
            self._latest = Frame(image, capture_t, self._seq)
            self._times.append(time.perf_counter())
        self.frames += 1
        if self.status != CameraStatus.ONLINE:
            self.height, self.width = image.shape[:2]
            self.status = CameraStatus.ONLINE
            log.info("cam%d: online %dx%d", self.camera_id, self.width, self.height)
        self.first_frame.set()

    def _read_file(self) -> None:
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps != fps or fps <= 0 or fps > 240:
            fps = DEFAULT_FILE_FPS
        self.source_fps = fps
        period = 1.0 / fps
        t0 = time.perf_counter()
        i = 0                 # frame index in the continuous (looped) stream
        in_pass = 0           # frames read since the file was (re)opened
        while not self._stop_event.is_set():
            target = t0 + i * period
            now = time.perf_counter()
            if target - now > 0 and self._stop_event.wait(target - now):
                return
            ok = self._cap.grab()  # decode only; the BGR copy is made below, and only for frames we publish
            if ok and time.perf_counter() > target + period:
                # Behind real time (e.g. CPU contention): this frame's slot is over, so drop it like a live
                # camera would, instead of publishing old frames faster than 25 FPS to catch up.
                self.dropped += 1
                in_pass += 1
                i += 1
                behind = time.perf_counter() - (t0 + i * period)
                if behind > MAX_BEHIND_S:  # hopelessly behind: skip ahead to "now"
                    log.warning("cam%d: file reader %.1f s behind, skipping ahead", self.camera_id, behind)
                    t0 += behind
                continue
            img = self._cap.retrieve()[1] if ok else None
            if not ok or img is None:
                if in_pass == 0:
                    log.warning("cam%d: file gave no frames", self.camera_id)
                    return  # -> reconnect
                self._release()  # end of file: replay from the start
                if not self._open():
                    return
                self.loops += 1
                in_pass = 0
                continue
            self._publish(img, target)
            in_pass += 1
            i += 1

    def _read_stream(self) -> None:
        failures = 0
        while not self._stop_event.is_set():
            ok, img = self._cap.read()
            if not ok or img is None:
                failures += 1
                if failures >= self.max_read_failures:
                    log.warning("cam%d: %d read failures in a row", self.camera_id, failures)
                    return  # -> reconnect
                self._stop_event.wait(0.05)
                continue
            failures = 0
            self._publish(img, time.perf_counter())
