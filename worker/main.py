"""RADS pipeline worker – entry point.

Phase 1: camera readers + detector + scheduler with an optional preview window and timing logs.

    .venv/bin/python -m worker.main --source1 <file|rtsp> --source2 <file|rtsp> --show

Each camera is processed every `--interval` seconds (default 0.2 = 5 FPS), always on its newest frame. Every
`--report-every` seconds the per-camera detect / total / lag times and the loop busy % are logged to
rads_data/logs/worker.log (and the console). Stop with Ctrl+C, SIGTERM, or `q`/Esc in the preview window.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python worker/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import logging
import math
import signal
import threading
import time
from logging.handlers import RotatingFileHandler

import cv2

from shared.config import get_settings
from worker.detector import Detector
from worker.draw import draw_detections, draw_overlay, resize_for_display
from worker.perf import PerfWindow, advance_slot
from worker.reader import CameraReader, redact

log = logging.getLogger("worker.main")

FIRST_FRAME_TIMEOUT_S = 15.0
MAX_CONSECUTIVE_ERRORS = 50
GUI_PUMP_MIN_IDLE_S = 0.025  # pump the preview window only when at least this much idle time is left
GUI_PUMP_MAX_GAP_S = 1.0     # ...but at least once a second so the window stays responsive


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = RotatingFileHandler(logs_dir / "worker.log", maxBytes=5 * 2**20, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def _fmt(st: dict, keys: tuple[str, ...]) -> str:
    return " ".join(f"{k}={'-' if st[k] is None else st[k]}" for k in keys)


def log_summary(title: str, summary: dict, readers: dict[int, CameraReader]) -> None:
    for c, s in summary["cams"].items():
        r = readers[c]
        log.info("%s cam%d status=%s in_fps=%.1f processed=%d skipped=%d stale=%d errors=%d | "
                 "detect_ms %s | total_ms %s | lag_ms %s",
                 title, c, r.status.value, r.fps, s["processed"], s["skipped"], s["stale"], s["errors"],
                 _fmt(s["detect_ms"], ("mean", "p95")), _fmt(s["total_ms"], ("mean", "p95")),
                 _fmt(s["lag_ms"], ("p50", "p95", "max")))
    log.info("%s loop busy=%.1f%% (preview window %.1f%%) over %.1f s",
             title, summary["busy_pct"], summary["gui_pct"], summary["wall_s"])


class Scheduler:
    """Runs in the main thread (cv2.imshow must, on macOS). Never blocks on disk, DB or network."""

    def __init__(self, readers: dict[int, CameraReader], detector: Detector, *, interval: float, show: bool,
                 display_width: int, report_every: float, duration: float, stop: threading.Event) -> None:
        self.readers = readers
        self.detector = detector
        self.interval = interval
        self.show = show
        self.display_width = display_width
        self.report_every = report_every
        self.duration = duration
        self.stop = stop
        self._last_seq = {c: 0 for c in readers}
        self._last = {c: (0.0, 0) for c in readers}  # (detect ms, n detections) for the overlay
        self._windows: set[str] = set()
        self._errors = 0
        self._last_pump = 0.0

    def run(self) -> dict:
        cams = list(self.readers)
        n = len(cams)
        t0 = time.perf_counter()
        next_t = {c: t0 + i * self.interval / n for i, c in enumerate(cams)}  # staggered slots
        window = PerfWindow(cams, t0)
        total = PerfWindow(cams, t0)
        next_report = t0 + self.report_every
        end = t0 + self.duration if self.duration > 0 else math.inf

        while not self.stop.is_set():
            c = min(cams, key=next_t.__getitem__)
            slot = next_t[c]
            if slot >= end:
                break
            now = time.perf_counter()
            if now >= next_report:
                log_summary("perf", window.summary(now), self.readers)
                window.reset(now)
                while next_report <= now:
                    next_report += self.report_every
            if self.show and (slot - now > GUI_PUMP_MIN_IDLE_S or now - self._last_pump > GUI_PUMP_MAX_GAP_S):
                g = time.perf_counter()
                self._pump_gui()
                now = time.perf_counter()
                window.add_gui(now - g)
                total.add_gui(now - g)
            if now < slot:
                if self.stop.wait(slot - now):
                    break
                now = time.perf_counter()
            slot, skipped = advance_slot(slot, now, self.interval)
            if skipped:  # this camera's window(s) passed before we could start
                window.skip(c, skipped)
                total.skip(c, skipped)
                next_t[c] = slot
                continue
            self._process(c, window, total)
            next_t[c] = slot + self.interval

        now = time.perf_counter()
        last = window.summary(now)
        if any(s["processed"] for s in last["cams"].values()):  # the partial last window
            log_summary("perf", last, self.readers)
        summary = total.summary(now)
        log_summary("perf-total", summary, self.readers)
        return summary

    def _process(self, c: int, window: PerfWindow, total: PerfWindow) -> None:
        fr = self.readers[c].latest()
        if fr is None or fr.seq == self._last_seq[c]:  # no new frame since the last one we processed
            window.stale(c)
            total.stale(c)
            return
        self._last_seq[c] = fr.seq
        t_start = time.perf_counter()
        try:
            dets = self.detector.detect(fr.image)
            t_det = time.perf_counter()
            self._last[c] = ((t_det - t_start) * 1e3, len(dets))
            if self.show:
                self._show(c, fr.image, dets)
            self._errors = 0
        except Exception:
            log.exception("cam%d: frame processing failed", c)
            window.error(c)
            total.error(c)
            self._errors += 1
            if self._errors >= MAX_CONSECUTIVE_ERRORS:
                log.error("%d consecutive processing errors, stopping", self._errors)
                self.stop.set()
            return
        t_end = time.perf_counter()
        for w in (window, total):
            w.record(c, detect_ms=(t_det - t_start) * 1e3, total_ms=(t_end - t_start) * 1e3,
                     lag_ms=(t_end - fr.capture_t) * 1e3)

    def _show(self, c: int, image, dets) -> None:
        img, scale = resize_for_display(image, self.display_width)
        draw_detections(img, dets, scale)
        r = self.readers[c]
        det_ms, n = self._last[c]
        draw_overlay(img, [f"cam{c} {r.status.value}  in {r.fps:.1f} fps",
                           f"detect {det_ms:.0f} ms  boxes {n}  {self.detector.device}"])
        name = f"RADS cam{c}"
        if name not in self._windows:
            cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE)
            cv2.moveWindow(name, 20 + len(self._windows) * (img.shape[1] + 10), 40)
            self._windows.add(name)
        cv2.imshow(name, img)  # painted by the next _pump_gui()

    def _pump_gui(self) -> None:
        """Run the window event loop (~10 ms on macOS). Called in the idle gap before a slot, not per frame."""
        self._last_pump = time.perf_counter()
        if not self._windows:
            return
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            log.info("quit key pressed")
            self.stop.set()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="worker.main", description="RADS pipeline worker")
    p.add_argument("--source1", help="camera 1: video file or RTSP URL (default: CAM1_RTSP from .env)")
    p.add_argument("--source2", help="camera 2: video file or RTSP URL (default: CAM2_RTSP from .env)")
    p.add_argument("--interval", type=float, default=0.2, help="seconds between frames per camera (0.2 = 5 FPS)")
    p.add_argument("--show", action="store_true", help="show a preview window per camera")
    p.add_argument("--display-width", type=int, default=960, help="preview window width in pixels")
    p.add_argument("--duration", type=float, default=0, help="stop after this many seconds (0 = run until stopped)")
    p.add_argument("--report-every", type=float, default=30, help="seconds between timing log lines")
    p.add_argument("--retry", type=float, default=10, help="seconds between reconnect attempts")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings.logs_dir)

    sources = {1: args.source1 or settings.camera_rtsp("CAM1_RTSP"),
               2: args.source2 or settings.camera_rtsp("CAM2_RTSP")}
    sources = {c: s for c, s in sources.items() if s}
    if not sources:
        log.error("no camera sources: pass --source1/--source2 or set CAM1_RTSP/CAM2_RTSP")
        return 2
    if args.interval <= 0:
        log.error("--interval must be > 0")
        return 2

    stop = threading.Event()

    def _on_signal(signum, _frame):
        log.info("signal %s received, stopping", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    log.info("worker starting: cameras=%s interval=%.3f s show=%s device setting=%r model=%s imgsz=%d",
             {c: redact(s) for c, s in sources.items()}, args.interval, args.show, settings.device,
             settings.model_path.name, settings.imgsz)

    readers = {c: CameraReader(c, s, retry_s=args.retry) for c, s in sources.items()}
    for r in readers.values():
        r.start()
    try:
        detector = Detector(settings.model_path, settings.imgsz, settings.device)

        deadline = time.perf_counter() + FIRST_FRAME_TIMEOUT_S
        for c, r in readers.items():
            while not stop.is_set() and not r.first_frame.wait(0.2) and time.perf_counter() < deadline:
                pass
            if not r.first_frame.is_set():
                log.warning("cam%d: no frame after %.0f s, continuing without it for now", c, FIRST_FRAME_TIMEOUT_S)
        if stop.is_set():
            return 0
        first = next((r.latest() for r in readers.values() if r.latest() is not None), None)
        detector.warmup(first.image if first else None)

        Scheduler(readers, detector, interval=args.interval, show=args.show, display_width=args.display_width,
                  report_every=args.report_every, duration=args.duration, stop=stop).run()
    except Exception:
        log.exception("worker failed")
        return 1
    finally:
        for r in readers.values():
            r.stop()
        for r in readers.values():
            r.join(timeout=7)
        if args.show:
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
