"""RADS pipeline worker – entry point.

    .venv/bin/python -m worker.main --source1 <file|rtsp> --source2 <file|rtsp> --show

Cameras, zones, hazards and rules come from the database (run scripts/init_db.py and scripts/seed.py first).
With no --sourceN, every enabled camera in the DB is opened at its rtsp_url; with any --sourceN, only the
cameras given on the command line run (camera N uses camera N's zones and rules).

Each camera is processed every `--interval` seconds (default 0.2 = 5 FPS), always on its newest frame:
detect -> ignore-zone policy -> track kept persons (ByteTrack per camera) -> link head/mask/apron -> zones ->
rule engine. Incidents, evidence JPEGs, camera_health and the API messages are handled by the notifier threads, so
the loop never waits on disk, database or network. Every `--report-every` seconds the timings, loop busy % and
track counts are logged to rads_data/logs/worker.log.
Stop with Ctrl+C, SIGTERM, or `q`/Esc in the preview window: open incidents are closed and the notifier finishes
its queue before the process exits.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python worker/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import logging
import math
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from typing import Any

import cv2

from shared.config import get_settings
from shared.contract import HEARTBEAT_INTERVAL_S, TiedTo, ZoneType
from worker.detector import Detector
from worker.draw import (DARK_GREY, GREY, draw_detections, draw_overlay, draw_workers, draw_zones,
                         resize_for_display)
from worker.linking import Worker, link_parts
from worker.notifier import CloseJob, HeartbeatJob, Notifier, OpenJob
from worker.perf import PerfWindow, advance_slot
from worker.reader import CameraReader, redact
from worker.rules import Event, IncidentOpen, RuleEngine
from worker.settings import WorkerSettings, Zone, load_settings
from worker.tracking import PERSON, PersonTracker
from worker.zones import CameraZones

log = logging.getLogger("worker.main")

FIRST_FRAME_TIMEOUT_S = 15.0
MAX_CONSECUTIVE_ERRORS = 50
GUI_PUMP_MIN_IDLE_S = 0.025  # pump the preview window only when at least this much idle time is left
GUI_PUMP_MAX_GAP_S = 1.0     # ...but at least once a second so the window stays responsive


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> QueueListener:
    """File (rotating) + console logging. Records go through a queue to a listener thread, so a log call in
    the main loop never waits on disk. Call .stop() on the returned listener at exit."""
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
    for noisy in ("httpx", "httpcore"):  # one INFO line per request otherwise; the notifier logs what matters
        logging.getLogger(noisy).setLevel(logging.WARNING)
    q: queue.SimpleQueue = queue.SimpleQueue()
    listener = QueueListener(q, fh, sh)
    root.addHandler(QueueHandler(q))
    listener.start()
    return listener


# --------------------------------------------------------------------------- per-camera pipeline


@dataclass
class FrameResult:
    """Everything one processed frame produced; the rule engine (Phase 3) consumes this."""
    camera_id: int
    capture_t: float
    shape: tuple[int, int]
    dets: list[dict[str, Any]]                              # kept: persons, objects, parts not dropped
    workers: list[Worker]                                   # tracked persons with linked parts and zone_ids
    objects: list[tuple[dict[str, Any], tuple[int, ...]]]   # zone-hazard objects (phone, bins) with zone ids
    untracked: list[dict[str, Any]]                         # kept person detections without a confirmed track yet
    ignored_persons: list[dict[str, Any]]                   # feet in an ignore zone
    dropped_parts: list[dict[str, Any]]                     # parts that belonged to an ignored person
    removed: list[dict[str, Any]]                           # objects removed by the ignore policy
    lost_track_ids: list[int] = field(default_factory=list)  # tracks the tracker dropped this frame

    def in_prep_count(self, prep_ids: set[int]) -> int:
        return sum(1 for w in self.workers if prep_ids.intersection(w.zone_ids))


class CameraPipeline:
    """Tracker + zones of one camera; turns detections into a FrameResult. Pure CPU work, no I/O."""

    def __init__(self, camera_id: int, zones: list[Zone], fps: float, object_classes: set[str]) -> None:
        self.camera_id = camera_id
        self.zones = CameraZones(zones)
        self.tracker = PersonTracker(camera_id, fps=fps)
        self.object_classes = object_classes
        self.prep_ids = {z.zone_id for z in self.zones.of_type(ZoneType.PREP)}
        self._seen_ids: set[int] = set()
        self._checked = False
        if not self.zones.zones:
            log.warning("cam%d: no active zones – nobody will be in a zone", camera_id)

    def reset(self) -> None:
        """Camera restart (clip looped): forget all tracks; zones stay."""
        self.tracker.reset()

    def check_frame(self, width: int, height: int) -> None:
        for fc in self.zones.check_frame(width, height):
            z = next(z for z in self.zones.zones if z.zone_id == fc.zone_id)
            if fc.outside:
                log.warning("cam%d: zone %d '%s' (%s) goes outside the %dx%d frame: %s",
                            self.camera_id, z.zone_id, z.name, z.type, width, height, list(z.polygon))
            log.info("cam%d: zone %d '%s' (%s) covers %.1f%% of the %dx%d frame",
                     self.camera_id, z.zone_id, z.name, z.type, fc.coverage_pct, width, height)

    def process(self, dets: list[dict[str, Any]], shape: tuple[int, int], capture_t: float) -> tuple[FrameResult, int]:
        """Returns the frame result and how many track IDs appeared for the first time."""
        if not self._checked:
            self._checked = True
            self.check_frame(shape[1], shape[0])
        ig = self.zones.apply_ignore(dets)
        tracked = self.tracker.update(ig.persons, shape)
        dropped: list[dict[str, Any]] = []
        workers = link_parts(tracked, ig.parts, ignored=[d["xyxy"] for d in ig.ignored_persons], dropped=dropped)
        for w in workers:
            w.zone_ids = self.zones.person_zones(w.box)
        objects = [(d, self.zones.object_zones(d["xyxy"])) for d in ig.objects if d["cls_name"] in self.object_classes]
        tracked_boxes = {p.box for p in tracked}
        untracked = [d for d in ig.persons if tuple(d["xyxy"]) not in tracked_boxes]
        dropped_ids = {id(d) for d in dropped}
        kept = ig.persons + ig.objects + [d for d in ig.parts if id(d) not in dropped_ids]
        ids = {w.track_id for w in workers}
        new_ids = len(ids - self._seen_ids)
        self._seen_ids |= ids
        return FrameResult(self.camera_id, capture_t, shape, kept, workers, objects, untracked,
                           ig.ignored_persons, dropped, ig.removed, list(self.tracker.removed_ids)), new_ids


def annotate(image, result: FrameResult, pipeline: CameraPipeline, width: int, lines: list[str]):
    """The preview image: zones, kept detections, ignored items (dark grey), workers with IDs/states, overlay."""
    img, scale = resize_for_display(image, width)
    draw_zones(img, pipeline.zones.zones, scale)
    draw_detections(img, result.ignored_persons + result.dropped_parts + result.removed, scale,
                    color=DARK_GREY, thickness=1, prefix="ign ")
    draw_detections(img, [d for d in result.dets if d["cls_name"] != PERSON], scale)
    draw_detections(img, result.untracked, scale, color=GREY, thickness=1)
    draw_workers(img, result.workers, scale)
    t = pipeline.tracker
    draw_overlay(img, lines + [f"tracks {len(result.workers)}  keep {t.max_lost_frames} fr = {t.keep_s_effective:.1f} s",
                               f"ignored: persons {len(result.ignored_persons)}  parts {len(result.dropped_parts)}"
                               f"  objects {len(result.removed)}"])
    return img


def restart_camera(pipeline: CameraPipeline, engine: RuleEngine | None, reason: str = "clip looped"):
    """Treat a video file looping back to its start like a camera restart: close the camera's open incidents
    (end_time = last violation) and reset its tracker, rule state and phone spots. Returns the close events."""
    closes = engine.restart(reason) if engine is not None else []
    pipeline.reset()
    return closes


def zone_object_classes(ws: WorkerSettings) -> set[str]:
    """Classes of the zone-tied hazards (phone, bin_open, bin_closed), from the hazards table."""
    out: set[str] = set()
    for h in ws.hazards.values():
        if h.tied_to == TiedTo.ZONE:
            out.add(h.violation_class)
            if h.compliant_class:
                out.add(h.compliant_class)
    return out


# --------------------------------------------------------------------------- logging of perf windows


def _fmt(st: dict, keys: tuple[str, ...]) -> str:
    return " ".join(f"{k}={'-' if st[k] is None else st[k]}" for k in keys)


def log_summary(title: str, summary: dict, readers: dict[int, CameraReader]) -> None:
    for c, s in summary["cams"].items():
        r = readers[c]
        log.info("%s cam%d status=%s in_fps=%.1f processed=%d skipped=%d stale=%d errors=%d | "
                 "detect_ms %s | post_ms %s | total_ms %s | lag_ms %s | tracks max_active=%d new_ids=%d | "
                 "worker_frames=%d in_prep=%s%% ignored_persons=%d dropped_parts=%d removed_objects=%d | "
                 "reader dropped=%d",
                 title, c, r.status.value, r.fps, s["processed"], s["skipped"], s["stale"], s["errors"],
                 _fmt(s["detect_ms"], ("mean", "p95")), _fmt(s["post_ms"], ("mean", "p95")),
                 _fmt(s["total_ms"], ("mean", "p95")), _fmt(s["lag_ms"], ("p50", "p95", "max")),
                 s["tracks_max_active"], s["new_track_ids"], s["worker_frames"],
                 "-" if s["in_prep_pct"] is None else s["in_prep_pct"], s["ignored_persons"], s["dropped_parts"],
                 s["removed_objects"], r.dropped)
    log.info("%s loop busy=%.1f%% (preview window %.1f%%) over %.1f s",
             title, summary["busy_pct"], summary["gui_pct"], summary["wall_s"])


# --------------------------------------------------------------------------- scheduler


class Scheduler:
    """Runs in the main thread (cv2.imshow must, on macOS). Never blocks on disk, DB or network."""

    def __init__(self, readers: dict[int, CameraReader], detector: Detector, pipelines: dict[int, CameraPipeline],
                 *, interval: float, show: bool, display_width: int, report_every: float, duration: float,
                 stop: threading.Event, engines: dict[int, RuleEngine] | None = None,
                 notifier: Notifier | None = None, settings: WorkerSettings | None = None) -> None:
        self.readers = readers
        self.engines = engines or {}
        self.notifier = notifier
        self.settings = settings
        self.detector = detector
        self.pipelines = pipelines
        self.interval = interval
        self.show = show
        self.display_width = display_width
        self.report_every = report_every
        self.duration = duration
        self.stop = stop
        self._last_seq = {c: 0 for c in readers}
        self._windows: set[str] = set()
        self._errors = 0
        self._last_pump = 0.0
        self._pass = {c: 0 for c in readers}       # clip pass last processed (video files)
        self._hb_frames = {c: 0 for c in readers}   # frames processed since the last heartbeat
        self._hb_last: dict[int, float] = {}
        self._hb_next: dict[int, float] = {}

    def run(self) -> dict:
        cams = list(self.readers)
        n = len(cams)
        t0 = time.perf_counter()
        next_t = {c: t0 + i * self.interval / n for i, c in enumerate(cams)}  # staggered slots
        window = PerfWindow(cams, t0)
        total = PerfWindow(cams, t0)
        next_report = t0 + self.report_every
        end = t0 + self.duration if self.duration > 0 else math.inf
        for c in cams:
            self._hb_last[c] = t0
            self._hb_next[c] = t0 + HEARTBEAT_INTERVAL_S

        while not self.stop.is_set():
            c = min(cams, key=next_t.__getitem__)
            slot = next_t[c]
            if slot >= end:
                break
            now = time.perf_counter()
            if now >= next_report:
                log_summary("perf", window.summary(now), self.readers)
                self._log_notifier()
                window.reset(now)
                while next_report <= now:
                    next_report += self.report_every
            self._heartbeats(now)
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
        self._log_notifier()
        return summary

    def _heartbeats(self, now: float) -> None:
        """Every HEARTBEAT_INTERVAL_S per camera: status + processed FPS to the notifier (camera_health + msg 6)."""
        if self.notifier is None:
            return
        for c, r in self.readers.items():
            if now < self._hb_next[c]:
                continue
            elapsed = now - self._hb_last[c]
            fps = self._hb_frames[c] / elapsed if elapsed > 0 else None
            self._hb_frames[c], self._hb_last[c] = 0, now
            self._hb_next[c] += HEARTBEAT_INTERVAL_S
            if self._hb_next[c] <= now:
                self._hb_next[c] = now + HEARTBEAT_INTERVAL_S
            fr = r.latest()
            wall = time.time()
            last_seen = None if fr is None else wall - (time.perf_counter() - fr.capture_t)
            self.notifier.submit(HeartbeatJob(c, r.status, None if fps is None else round(fps, 2), wall, last_seen))

    def _log_notifier(self) -> None:
        if self.notifier is not None:
            n = self.notifier
            log.info("notifier queue db=%d http=%d dropped=%d | %s | takeovers %s | swap transfers %s",
                     len(n.jobs), len(n.posts), n.jobs.dropped + n.posts.dropped, dict(n.stats),
                     {f"cam{c}": e.takeovers for c, e in self.engines.items()},
                     {f"cam{c}": e.swap_transfers for c, e in self.engines.items()})

    def _submit(self, c: int, events: list[Event], image, t_wall: float, result: FrameResult) -> None:
        for ev in events:
            if isinstance(ev, IncidentOpen):
                zones = [z for z in self.pipelines[c].zones.zones if z.zone_id in ev.rule.zone_ids]
                name = self.settings.hazards[ev.hazard_id].name if self.settings else ev.hazard_id
                self.notifier.submit(OpenJob(ev, image.copy(), t_wall, list(result.dets), list(result.workers),
                                             zones, name))
            else:
                self.notifier.submit(CloseJob(ev))

    def _process(self, c: int, window: PerfWindow, total: PerfWindow) -> None:
        fr = self.readers[c].latest()
        if fr is None or fr.seq == self._last_seq[c]:  # no new frame since the last one we processed
            window.stale(c)
            total.stale(c)
            return
        self._last_seq[c] = fr.seq
        if fr.loop != self._pass[c] and self.readers[c].is_file:
            self._pass[c] = fr.loop
            closes = restart_camera(self.pipelines[c], self.engines.get(c))
            if self.notifier is not None:
                for ev in closes:
                    self.notifier.submit(CloseJob(ev))
            log.info("cam%d: clip looped – pass %d starts; closed %d open incident(s), tracker, rule state and "
                     "phone spots reset", c, fr.loop + 1, len(closes))
        t_start = time.perf_counter()
        try:
            dets = self.detector.detect(fr.image)
            t_det = time.perf_counter()
            result, new_ids = self.pipelines[c].process(dets, fr.image.shape[:2], fr.capture_t)
            if c in self.engines:
                t_wall = time.time() - (time.perf_counter() - fr.capture_t)  # capture time as epoch seconds
                events = self.engines[c].update(t_wall, result.workers, result.objects, result.lost_track_ids)
                if events and self.notifier is not None:
                    self._submit(c, events, fr.image, t_wall, result)
            t_post = time.perf_counter()
            self._hb_frames[c] += 1
            if self.show:
                self._show(c, fr.image, result, (t_det - t_start) * 1e3)
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
                     lag_ms=(t_end - fr.capture_t) * 1e3, post_ms=(t_post - t_det) * 1e3)
            w.note_tracks(c, active=len(result.workers), new_ids=new_ids)
            w.note_zones(c, workers=len(result.workers), in_prep=result.in_prep_count(self.pipelines[c].prep_ids),
                         ignored_persons=len(result.ignored_persons), dropped_parts=len(result.dropped_parts),
                         removed_objects=len(result.removed))

    def _show(self, c: int, image, result: FrameResult, det_ms: float) -> None:
        r = self.readers[c]
        img = annotate(image, result, self.pipelines[c], self.display_width,
                       [f"cam{c} {r.status.value}  in {r.fps:.1f} fps  {self.detector.device}",
                        f"detect {det_ms:.0f} ms  boxes {len(result.dets)}"])
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


# --------------------------------------------------------------------------- entry point


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="worker.main", description="RADS pipeline worker")
    p.add_argument("--source1", help="camera 1: video file or RTSP URL (default: camera 1's rtsp_url in the DB)")
    p.add_argument("--source2", help="camera 2: video file or RTSP URL (default: camera 2's rtsp_url in the DB)")
    p.add_argument("--interval", type=float, default=0.2, help="seconds between frames per camera (0.2 = 5 FPS)")
    p.add_argument("--show", action="store_true", help="show a preview window per camera")
    p.add_argument("--display-width", type=int, default=960, help="preview window width in pixels")
    p.add_argument("--duration", type=float, default=0, help="stop after this many seconds (0 = run until stopped)")
    p.add_argument("--report-every", type=float, default=30, help="seconds between timing log lines")
    p.add_argument("--retry", type=float, default=10, help="seconds between reconnect attempts")
    return p.parse_args(argv)


def pick_sources(args: argparse.Namespace, ws: WorkerSettings) -> dict[int, str]:
    """CLI sources if any were given (only those cameras run), else every enabled DB camera's rtsp_url."""
    cli = {c: s for c, s in ((1, args.source1), (2, args.source2)) if s}
    if cli:
        for c in cli:
            if c not in ws.cameras:
                log.warning("camera %d is not in the database: it has no zones or rules", c)
        return cli
    return {c.camera_id: c.rtsp_url for c in ws.cameras.values() if c.enabled and c.rtsp_url}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    listener = setup_logging(settings.logs_dir)
    try:
        return _run(args, settings)
    finally:
        listener.stop()


def _run(args: argparse.Namespace, settings) -> int:
    if args.interval <= 0:
        log.error("--interval must be > 0")
        return 2

    if not settings.db_path.exists():
        log.error("database %s not found: run scripts/init_db.py and scripts/seed.py first", settings.db_path)
        return 2
    try:
        ws = load_settings()
    except Exception:
        log.exception("cannot read settings from %s: run scripts/init_db.py and scripts/seed.py first",
                      settings.db_path)
        return 2

    sources = pick_sources(args, ws)
    if not sources:
        log.error("no camera sources: pass --source1/--source2 or enable cameras in the database")
        return 2

    stop = threading.Event()

    def _on_signal(signum, _frame):
        log.info("signal %s received, stopping", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    fps = 1.0 / args.interval
    log.info("worker starting: cameras=%s interval=%.3f s (%.1f FPS) show=%s device setting=%r model=%s imgsz=%d",
             {c: redact(s) for c, s in sources.items()}, args.interval, fps, args.show, settings.device,
             settings.model_path.name, settings.imgsz)

    readers = {c: CameraReader(c, s, retry_s=args.retry) for c, s in sources.items()}
    for r in readers.values():
        r.start()
    notifier: Notifier | None = None
    engines: dict[int, RuleEngine] = {}
    try:
        obj_classes = zone_object_classes(ws)
        pipelines = {c: CameraPipeline(c, ws.zones_for(c), fps, obj_classes) for c in sources}
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

        engines = {c: RuleEngine(c, ws.rules_for(c), ws.hazards, fps,
                                 track_keep_s=pipelines[c].tracker.keep_s_effective) for c in sources}
        for c, e in engines.items():
            log.info("cam%d rules: %s", c, "; ".join(
                f"{r.hazard_id} zones={list(r.zone_ids)} conf>={r.conf_threshold} window={r.window_s:g}s "
                f"ratio>={r.min_ratio} end_gap={r.end_gap_s:g}s cooldown={r.cooldown_s:g}s {r.severity}"
                for r in e.rules) or "none")
        if not settings.internal_token:
            log.warning("RADS_INTERNAL_TOKEN is empty: the API will reject the worker's messages")
        notifier = Notifier(evidence_dir=settings.evidence_dir, api_url=settings.api_url,
                            token=settings.internal_token, model_version=detector.model_version)
        notifier.start()

        Scheduler(readers, detector, pipelines, interval=args.interval, show=args.show,
                  display_width=args.display_width, report_every=args.report_every, duration=args.duration,
                  stop=stop, engines=engines, notifier=notifier, settings=ws).run()
    except Exception:
        log.exception("worker failed")
        return 1
    finally:
        for r in readers.values():
            r.stop()
        if notifier is not None:  # clean stop: close open incidents, then let the notifier finish
            closes = [ev for e in engines.values() for ev in e.close_all(time.time())]
            for ev in closes:
                notifier.submit(CloseJob(ev))
            log.info("stopping: closing %d open incident(s), flushing the notifier", len(closes))
            notifier.stop(timeout=30)
            log.info("notifier: %s", dict(notifier.stats))
        for r in readers.values():
            r.join(timeout=7)
        if args.show:
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
