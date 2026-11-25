"""CameraReader: one thread per camera that keeps only the newest frame.

The source is an RTSP URL or a video file path. A file is replayed at its real frame rate and looped at the end,
which is how the worker is tested without cameras. On any failure the reader goes to `reconnecting`, waits
`retry_s` (10 s) and tries again; it never raises out of the thread.

RTSP: always over TCP, with open / read timeouts both in FFmpeg (socket `timeout`) and in OpenCV (interrupt
callback), so a dead stream never blocks read() for long. No frame for STALL_S (5 s) -> `reconnecting`.
Every (re)connection gets a number (Frame.conn): the scheduler resets that camera's tracker and rule state when it
changes, like a clip loop.
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
RTSP_READ_TIMEOUT_MS = 5_000   # one read() never blocks longer than this
STALL_S = 5.0                  # no frame for this long -> reconnecting
RTSP_FFMPEG_OPTIONS = {"rtsp_transport": "tcp", "timeout": str(RTSP_READ_TIMEOUT_MS * 1000)}  # timeout in µs
_env_lock = threading.Lock()


def ffmpeg_capture_options(existing: str | None) -> str:
    """OPENCV_FFMPEG_CAPTURE_OPTIONS ("key;value|key;value") with ours forced on top of any the user set."""
    opts: dict[str, str] = {}
    for item in (existing or "").split("|"):
        k, sep, v = item.partition(";")
        if sep and k.strip():
            opts[k.strip()] = v.strip()
    opts.update(RTSP_FFMPEG_OPTIONS)
    return "|".join(f"{k};{v}" for k, v in opts.items())


def redact(source: str) -> str:
    """Hide credentials in an RTSP URL before it is logged: rtsp://user:pass@host -> rtsp://***@host."""
    return re.sub(r"//[^/@]+@", "//***@", source)


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    capture_t: float  # time.perf_counter() of capture (nominal frame time for a file)
    seq: int          # increases by one for every new frame from this reader
    loop: int = 0     # video files: how many times the file looped back to its start (0 = first pass)
    conn: int = 1     # connection number: +1 every time the source (re)connects


class CameraReader(threading.Thread):
    def __init__(self, camera_id: int, source: str | os.PathLike, *, retry_s: float = 10.0,
                 stall_s: float = STALL_S) -> None:
        super().__init__(daemon=True, name=f"reader-cam{camera_id}")
        self.camera_id = camera_id
        src = str(source)
        path = Path(src).expanduser()
        self.is_file = path.is_file()
        self.source = str(path) if self.is_file else src
        self.retry_s = retry_s
        self.stall_s = stall_s

        self.status: CameraStatus = CameraStatus.OFFLINE
        self.frames = 0          # frames read since start
        self.loops = 0           # times a file was replayed from the start
        self.dropped = 0         # file frames skipped because the reader was behind real time
        self.reconnects = 0      # times the reader went to `reconnecting`
        self.connects = 0        # times the source came online (first frame after an open)
        self.codec: str | None = None
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
            with _env_lock:  # OpenCV reads the options from the environment when the capture opens
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_capture_options(
                    os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"))
                cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG,
                                       [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_OPEN_TIMEOUT_MS,
                                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, RTSP_READ_TIMEOUT_MS])
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            log.warning("cam%d: cannot open %s", self.camera_id, redact(self.source))
            return False
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.source_fps = fps if fps and fps == fps and 0 < fps <= 240 else None
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        self.codec = fourcc.to_bytes(4, "little").decode("ascii", "replace").strip("\x00 ") if fourcc else None
        self._cap = cap
        return True

    def _release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _publish(self, image: np.ndarray, capture_t: float) -> None:
        if self.status != CameraStatus.ONLINE:
            self.connects += 1
            self.height, self.width = image.shape[:2]
            log.info("cam%d: connected (#%d): %dx%d, source %s fps, codec %s", self.camera_id, self.connects,
                     self.width, self.height, "?" if self.source_fps is None else f"{self.source_fps:.2f}",
                     self.codec or "?")
        with self._lock:
            self._seq += 1
            self._latest = Frame(image, capture_t, self._seq, self.loops, self.connects)
            self._times.append(time.perf_counter())
        self.frames += 1
        self.status = CameraStatus.ONLINE
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
        """Read until the stream stalls: no frame for stall_s, or one read() that ran into the read timeout."""
        last_ok = time.perf_counter()
        while not self._stop_event.is_set():
            t = time.perf_counter()
            ok, img = self._cap.read()
            now = time.perf_counter()
            if ok and img is not None:
                last_ok = now
                self._publish(img, now)
                continue
            timed_out = now - t >= 0.9 * RTSP_READ_TIMEOUT_MS / 1000
            if timed_out or now - last_ok >= self.stall_s:
                log.warning("cam%d: no frame for %.1f s – stream stalled", self.camera_id, now - last_ok)
                return  # -> reconnecting
            self._stop_event.wait(0.05)
