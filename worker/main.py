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
A web server thread on 127.0.0.1:8001 serves the live view (GET /stream/{camera_id}, MJPEG), POST /reload (token;
the scheduler re-reads the settings before its next frame and resets only what changed) and GET /health.
Stop with Ctrl+C, SIGTERM, or `q`/Esc in the preview window: open incidents are closed and the notifier finishes
its queue before the process exits.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python worker/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import csv
import logging
import math
import queue
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from typing import Any, Callable

import cv2

from shared.config import get_settings
from shared.contract import HEARTBEAT_INTERVAL_S, CameraStatus, TiedTo, ZoneType
from worker.detector import Detector
from worker.draw import annotate as draw_annotate
from worker.linking import Worker, link_parts
from worker.notifier import CloseJob, HeartbeatJob, Notifier, OpenJob, iso_time
from worker.perf import PerfWindow, advance_slot
from worker.reader import CameraReader, redact
from worker.reload import diff, validate, zone_camera
from worker.rules import Event, IncidentOpen, RuleEngine
from worker.settings import WorkerSettings, Zone, load_settings
from worker.tracking import PERSON, PersonTracker
from worker.webserver import FrameHub, WebServer
from worker.zones import CameraZones

log = logging.getLogger("worker.main")

FIRST_FRAME_TIMEOUT_S = 15.0
MAX_CONSECUTIVE_ERRORS = 50
GUI_PUMP_MIN_IDLE_S = 0.025  # pump the preview window only when at least this much idle time is left
GUI_PUMP_MAX_GAP_S = 1.0     # ...but at least once a second so the window stays responsive
HANDOFF_S = 0.5              # frames + results to the web server, and the /health snapshot, every 0.5 s
OFFLINE_CLOSE_S = 60.0       # a camera not online for longer: close its open incidents, report it `offline`


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

    def set_zones(self, zones: list[Zone]) -> None:
        """Zones changed on reload: new polygons from the next frame on; tracks are kept."""
        self.zones = CameraZones(zones)
        self.prep_ids = {z.zone_id for z in self.zones.of_type(ZoneType.PREP)}
        self._checked = False  # log coverage / out-of-frame warnings again

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
    """The preview image (see draw.annotate) with the tracker and ignore-zone lines added to the overlay."""
    t = pipeline.tracker
    return draw_annotate(image, result, pipeline.zones.zones, width, lines + [
        f"tracks {len(result.workers)}  keep {t.max_lost_frames} fr = {t.keep_s_effective:.1f} s",
        f"ignored: persons {len(result.ignored_persons)}  parts {len(result.dropped_parts)}  objects {len(result.removed)}"])


def restart_camera(pipeline: CameraPipeline, engine: RuleEngine | None, reason: str = "clip looped"):
    """Treat a video file looping back to its start like a camera restart: close the camera's open incidents
    (end_time = last violation) and reset its tracker, rule state and phone spots. Returns the close events."""
    closes = engine.restart(reason) if engine is not None else []
    pipeline.reset()
    return closes


def build_engines(ws: WorkerSettings, pipelines: dict[int, CameraPipeline], fps: float,
                  crossing_check: bool = False) -> dict[int, RuleEngine]:
    """One rule engine per camera, with that camera's enabled rules and its tracker's keep time."""
    return {c: RuleEngine(c, ws.rules_for(c), ws.hazards, fps, track_keep_s=p.tracker.keep_s_effective,
                          crossing_check=crossing_check) for c, p in pipelines.items()}


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


class PerfCsv:
    """One row per report window (every --report-every s) in rads_data/logs/perf.csv, for Table 6.
    missed = processing slots without a processed frame (skipped: the loop was late; stale: no new camera frame;
    errors). CPU % is of one core (can go above 100 %), RSS is the worker's resident memory."""
    CAM_COLS = ("in_fps", "proc_fps", "processed", "missed", "skipped", "stale", "detect_mean_ms", "detect_p95_ms",
                "lag_p95_ms")

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.cams: list[int] | None = None
        try:
            import psutil
            self._proc = psutil.Process()
            self._proc.cpu_percent(None)  # start the CPU counter
        except Exception:  # noqa: BLE001 – psutil missing: memory/CPU columns stay empty
            self._proc = None

    def write(self, summary: dict, readers, engines, notifier) -> None:
        try:
            if self.cams is None:
                self.cams = sorted(summary["cams"])
            new = not self.path.exists() or self.path.stat().st_size == 0
            row = {"time": iso_time(time.time()), "wall_s": summary["wall_s"], "busy_pct": summary["busy_pct"]}
            for c in self.cams:
                s = summary["cams"].get(c)
                r = readers.get(c)
                if s is None:
                    continue
                missed = s["skipped"] + s["stale"] + s["errors"]
                row.update({f"cam{c}_in_fps": round(r.fps, 2) if r else "",
                            f"cam{c}_proc_fps": round(s["processed"] / max(summary["wall_s"], 1e-9), 2),
                            f"cam{c}_processed": s["processed"], f"cam{c}_missed": missed,
                            f"cam{c}_skipped": s["skipped"], f"cam{c}_stale": s["stale"],
                            f"cam{c}_detect_mean_ms": s["detect_ms"]["mean"], f"cam{c}_detect_p95_ms": s["detect_ms"].get("p95"),
                            f"cam{c}_lag_p95_ms": s["lag_ms"].get("p95")})
            if self._proc is not None:
                row["rss_mb"] = round(self._proc.memory_info().rss / 2**20, 1)
                row["cpu_pct"] = round(self._proc.cpu_percent(None), 1)
            row["open_incidents"] = sum(e.open_incident_count() for e in engines.values())
            row["queue_db"] = len(notifier.jobs) if notifier is not None else ""
            row["queue_http"] = len(notifier.posts) if notifier is not None else ""
            cols = (["time", "wall_s"] + [f"cam{c}_{k}" for c in self.cams for k in self.CAM_COLS]
                    + ["busy_pct", "rss_mb", "cpu_pct", "open_incidents", "queue_db", "queue_http"])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(row)
        except Exception:  # noqa: BLE001 – a CSV problem never stops the worker
            log.exception("perf.csv: cannot write %s", self.path)


def log_rules(engines: dict[int, RuleEngine]) -> None:
    for c, e in engines.items():
        log.info("cam%d rules: %s", c, "; ".join(
            f"{r.hazard_id} zones={list(r.zone_ids)} conf>={r.conf_threshold} window={r.window_s:g}s "
            f"ratio>={r.min_ratio} end_gap={r.end_gap_s:g}s cooldown={r.cooldown_s:g}s {r.severity}"
            for r in e.rules) or "none")


@dataclass
class Reloader:
    """What the scheduler needs to apply POST /reload."""
    load: Callable[[], WorkerSettings] = load_settings
    cli_sources: bool = False      # cameras from --sourceN are never restarted by a reload
    retry_s: float = 10.0
    crossing_check: bool = False

    def start_reader(self, camera_id: int, url: str) -> CameraReader:
        r = CameraReader(camera_id, url, retry_s=self.retry_s)
        r.start()
        return r


# --------------------------------------------------------------------------- scheduler


class Scheduler:
    """Runs in the main thread (cv2.imshow must, on macOS). Never blocks on disk, DB or network."""

    def __init__(self, readers: dict[int, CameraReader], detector: Detector, pipelines: dict[int, CameraPipeline],
                 *, interval: float, show: bool, display_width: int, report_every: float, duration: float,
                 stop: threading.Event, engines: dict[int, RuleEngine] | None = None,
                 notifier: Notifier | None = None, settings: WorkerSettings | None = None,
                 hub: FrameHub | None = None, reload_event: threading.Event | None = None,
                 reloader: Reloader | None = None, crossing_check: bool = False,
                 perf_csv: Path | None = None) -> None:
        self.readers = readers
        self.hub = hub
        self.reload_event = reload_event
        self.reloader = reloader
        self.crossing_check = crossing_check
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
        t0 = time.perf_counter()
        self._next_t: dict[int, float] = {}                 # next processing slot per camera
        self._window = PerfWindow(list(readers), t0)       # current report window
        self._total = PerfWindow(list(readers), t0)        # whole run
        self._pub_last = {c: 0.0 for c in readers}         # last hand-off to the web server
        self._proc_times: dict[int, deque] = {c: deque() for c in readers}  # processed frames, last 5 s
        self._health_next = 0.0
        self._conn = {c: 0 for c in readers}               # reader connection number last seen (0 = none yet)
        self._down_since: dict[int, float] = {}            # perf_counter time a camera stopped being online
        self._offline: set[int] = set()                    # down > OFFLINE_CLOSE_S: incidents closed, `offline`
        self.perf_csv = PerfCsv(perf_csv) if perf_csv is not None else None

    def run(self) -> dict:
        cams = list(self.readers)
        n = len(cams)
        t0 = time.perf_counter()
        self._next_t = {c: t0 + i * self.interval / n for i, c in enumerate(cams)}  # staggered slots
        window, total = self._window, self._total
        window.reset(t0)
        total.reset(t0)
        next_report = t0 + self.report_every
        end = t0 + self.duration if self.duration > 0 else math.inf
        for c in cams:
            self._hb_last[c] = t0
            self._hb_next[c] = t0 + HEARTBEAT_INTERVAL_S

        while not self.stop.is_set():
            self._check_reload()  # before the next frame
            if not self._next_t:  # every camera disabled
                if self.stop.wait(0.2) or time.perf_counter() >= end:
                    break
                continue
            c = min(self._next_t, key=self._next_t.__getitem__)
            slot = self._next_t[c]
            if slot >= end:
                break
            now = time.perf_counter()
            if now >= next_report:
                summ = window.summary(now)
                log_summary("perf", summ, self.readers)
                self._log_notifier()
                if self.perf_csv is not None:
                    self.perf_csv.write(summ, self.readers, self.engines, self.notifier)
                window.reset(now)
                while next_report <= now:
                    next_report += self.report_every
            self._check_offline(now)
            self._heartbeats(now)
            self._publish_health(now)
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
                self._next_t[c] = slot
                continue
            self._process(c, window, total)
            if c in self._next_t:
                self._next_t[c] = slot + self.interval

        now = time.perf_counter()
        last = window.summary(now)
        if any(s["processed"] for s in last["cams"].values()):  # the partial last window
            log_summary("perf", last, self.readers)
        summary = total.summary(now)
        log_summary("perf-total", summary, self.readers)
        self._log_notifier()
        return summary

    def _publish_health(self, now: float) -> None:
        """Every 0.5 s: a /health snapshot for the web server (built here, so the server never reads live state)."""
        if self.hub is None or now < self._health_next:
            return
        self._health_next = now + HANDOFF_S
        wall = time.time()
        cams = {}
        for c, r in self.readers.items():
            dq = self._proc_times.setdefault(c, deque())
            while dq and now - dq[0] > 5.0:
                dq.popleft()
            fr = r.latest()
            cams[c] = {"status": self.status(c).value, "input_fps": round(r.fps, 1), "processed_fps": round(len(dq) / 5.0, 1),
                       "last_frame": None if fr is None else iso_time(wall - (now - fr.capture_t)),
                       "open_incidents": self.engines[c].open_incident_count() if c in self.engines else 0}
        self.hub.set_health({"crossing_check": self.crossing_check, "cameras": cams})

    def status(self, c: int) -> CameraStatus:
        """The reader's status, but `offline` once the camera has been down longer than OFFLINE_CLOSE_S."""
        return CameraStatus.OFFLINE if c in self._offline else self.readers[c].status

    def _check_offline(self, now: float) -> None:
        """A camera not online for more than OFFLINE_CLOSE_S: close its open incidents (end_time = last violation,
        message 5) and reset its state, once per outage. Heartbeats then report it `offline`."""
        for c, r in self.readers.items():
            if r.status == CameraStatus.ONLINE:
                if c in self._down_since:
                    self._down_since.pop(c)
                    self._offline.discard(c)
                continue
            if c not in self._down_since:
                fr = r.latest()  # down since its last frame, if it ever had one
                self._down_since[c] = min(now, fr.capture_t) if fr is not None else now
            down = now - self._down_since[c]
            if c in self._offline or down <= OFFLINE_CLOSE_S:
                continue
            self._offline.add(c)
            closes = restart_camera(self.pipelines[c], self.engines.get(c), "camera offline")
            self._submit_closes(closes)
            log.warning("cam%d: not online for %.0f s (%s) – closed %d open incident(s) (end_time = last violation); "
                        "reported offline until it is back", c, down, r.status.value, len(closes))

    def _submit_closes(self, closes) -> None:
        if self.notifier is not None:
            for ev in closes:
                self.notifier.submit(CloseJob(ev))

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
            self.notifier.submit(HeartbeatJob(c, self.status(c), None if fps is None else round(fps, 2), wall, last_seen))

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

    def _check_frame_size(self, c: int, image) -> None:
        cam = self.settings.cameras.get(c) if self.settings is not None else None
        h, w = image.shape[:2]
        if cam is not None and cam.frame_width and cam.frame_height and (w, h) != (cam.frame_width,
                                                                               cam.frame_height):
            log.warning("cam%d: the source sends %dx%d but the DB says %dx%d – zones are in DB pixels and will be "
                        "in the wrong place; fix the camera's picture size or redraw the zones", c, w, h,
                        cam.frame_width, cam.frame_height)

    def _process(self, c: int, window: PerfWindow, total: PerfWindow) -> None:
        fr = self.readers[c].latest()
        if fr is None or fr.seq == self._last_seq[c]:  # no new frame since the last one we processed
            window.stale(c)
            total.stale(c)
            return
        self._last_seq[c] = fr.seq
        if fr.conn != self._conn.get(c, 0):
            first = self._conn.get(c, 0) == 0
            self._conn[c], self._pass[c] = fr.conn, fr.loop
            self._check_frame_size(c, fr.image)
            if not first:  # reconnected: new track ids and a time gap, so start this camera afresh
                closes = restart_camera(self.pipelines[c], self.engines.get(c), "camera reconnected")
                self._submit_closes(closes)
                log.info("cam%d: reconnected – closed %d open incident(s) (end_time = last violation); tracker, "
                         "rule state and phone spots reset", c, len(closes))
        elif fr.loop != self._pass[c] and self.readers[c].is_file:
            self._pass[c] = fr.loop
            closes = restart_camera(self.pipelines[c], self.engines.get(c))
            self._submit_closes(closes)
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
            self._proc_times.setdefault(c, deque()).append(t_post)
            if self.hub is not None and t_post - self._pub_last.get(c, 0.0) >= HANDOFF_S:
                self._pub_last[c] = t_post  # references only: the web server draws and encodes, if anyone watches
                r = self.readers[c]
                e = self.engines.get(c)
                self.hub.publish(c, fr.image, result, self.pipelines[c].zones.zones, [
                    f"cam{c} {r.status.value}  in {r.fps:.1f} fps  detect {(t_det - t_start) * 1e3:.0f} ms",
                    f"tracks {len(result.workers)}  open incidents {e.open_incident_count() if e else 0}"])
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

    # ------------------------------------------------------------------ reload (POST /reload)

    def _check_reload(self) -> None:
        if self.reload_event is not None and self.reload_event.is_set():
            self.reload_event.clear()
            self._reload()

    def _reload(self) -> None:
        """Re-read the settings and apply only what changed (see worker/reload.py for validation and diff)."""
        if self.reloader is None or self.settings is None:
            log.warning("reload requested, but this scheduler has no settings loader")
            return
        t_start = time.perf_counter()
        try:
            fresh = self.reloader.load()
        except Exception:
            log.exception("reload: cannot read the settings from the DB – keeping the current settings")
            return
        new, errors = validate(self.settings, fresh)
        d = diff(self.settings, new)
        now = time.time()
        closes = []
        zone_cams = {zone_camera(z, self.settings, new) for z in d.changed_zones}
        objects = zone_object_classes(new)
        for c, p in self.pipelines.items():
            p.object_classes = objects
            if c in zone_cams:
                p.set_zones(new.zones_for(c))
        for e in self.engines.values():
            closes += e.reload(new.rules.values(), new.hazards, d.changed_rules, d.closed_rules, d.changed_zones, now)
        restarted, added, removed, kept_cli = [], [], [], []
        for cid in d.changed_cameras:
            if self.reloader.cli_sources:
                kept_cli.append(cid)
                continue
            cam = new.cameras.get(cid)
            want = cam is not None and cam.enabled and bool(cam.rtsp_url)
            if cid in self.readers and not want:
                closes += self._remove_camera(cid, now)
                removed.append(cid)
            elif cid in self.readers:
                closes += self._restart_camera(cid, cam.rtsp_url)
                restarted.append(cid)
            elif want:
                self._add_camera(cid, cam.rtsp_url, new)
                added.append(cid)
        if self.notifier is not None:
            for ev in closes:
                self.notifier.submit(CloseJob(ev))
        self.settings = new
        if self.hub is not None:  # covers command-line cameras too: they keep running, /stream answers 404
            self.hub.set_disabled(disabled_cameras(new))
        ms = (time.perf_counter() - t_start) * 1e3
        if d.empty() and not errors:
            log.info("reload: nothing changed (%.0f ms)", ms)
            return
        log.info("reload: rules changed %s; rules closed %s; rules added %s; zones changed %s; cameras restarted %s, "
                 "added %s, removed %s, not restarted (command-line sources) %s; closed %d incident(s); "
                 "%d rules active, %d open incident(s) kept; %d error(s) (%.0f ms)",
                 [x for v in d.changed_rules.values() for x in v], list(d.closed_rules.values()),
                 sorted(d.added_rules), list(d.changed_zones.values()), restarted, added, removed, kept_cli,
                 len(closes), sum(len(e.rules) for e in self.engines.values()),
                 sum(e.open_incident_count() for e in self.engines.values()), len(errors), ms)
        log_rules(self.engines)

    def _restart_camera(self, cid: int, url: str) -> list:
        closes = restart_camera(self.pipelines[cid], self.engines.get(cid), "camera restarted")
        self.readers[cid].stop()  # no join: the old thread ends on its own
        self.readers[cid] = self.reloader.start_reader(cid, url)
        self._last_seq[cid], self._pass[cid], self._conn[cid] = 0, 0, 0
        self._down_since.pop(cid, None)
        self._offline.discard(cid)
        log.info("cam%d: source changed – reader restarted (%s)", cid, redact(url))
        return closes

    def _remove_camera(self, cid: int, now: float) -> list:
        e = self.engines.pop(cid, None)
        closes = e.close_all(now, "camera disabled") if e is not None else []
        self.readers.pop(cid).stop()
        self.pipelines.pop(cid, None)
        for dct in (self._next_t, self._hb_last, self._hb_next, self._last_seq, self._pass, self._pub_last,
                    self._proc_times, self._hb_frames, self._conn, self._down_since):
            dct.pop(cid, None)
        self._offline.discard(cid)
        if self.hub is not None:
            self.hub.set_cameras(self.readers)
        log.info("cam%d: disabled – reader stopped", cid)
        return closes

    def _add_camera(self, cid: int, url: str, ws: WorkerSettings) -> None:
        fps = 1.0 / self.interval
        self.readers[cid] = self.reloader.start_reader(cid, url)
        self.pipelines[cid] = CameraPipeline(cid, ws.zones_for(cid), fps, zone_object_classes(ws))
        self.engines[cid] = RuleEngine(cid, ws.rules_for(cid), ws.hazards, fps,
                                       track_keep_s=self.pipelines[cid].tracker.keep_s_effective,
                                       crossing_check=self.reloader.crossing_check)
        now = time.perf_counter()
        self._next_t[cid] = now
        self._hb_last[cid], self._hb_next[cid] = now, now + HEARTBEAT_INTERVAL_S
        self._last_seq[cid], self._pass[cid], self._pub_last[cid], self._hb_frames[cid] = 0, 0, 0.0, 0
        self._conn[cid] = 0
        self._proc_times[cid] = deque()
        self._window.add_camera(cid)
        self._total.add_camera(cid)
        if self.hub is not None:
            self.hub.set_cameras(self.readers)
        log.info("cam%d: enabled – reader started (%s)", cid, redact(url))

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
    p.add_argument("--crossing-check", action="store_true",
                   help="turn on the crossing check that moves an open incident across a pure tracker ID swap "
                        "(off by default)")
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


def disabled_cameras(ws: WorkerSettings) -> set[int]:
    """Cameras disabled in the DB. A --sourceN camera still runs, but its live view (/stream) answers 404."""
    return {c.camera_id for c in ws.cameras.values() if not c.enabled}


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
    stop_at: list[float] = []

    def _on_signal(signum, _frame):
        log.info("signal %s received, stopping", signal.Signals(signum).name)
        stop_at.append(time.monotonic())
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
    web: WebServer | None = None
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

        engines = build_engines(ws, pipelines, fps, crossing_check=args.crossing_check)
        log.info("crossing check (pure ID swaps): %s", "ON (--crossing-check, window = rule window_s + 10 s)"
                 if args.crossing_check else "OFF (default; turn on with --crossing-check)")
        log_rules(engines)
        if not settings.internal_token:
            log.warning("RADS_INTERNAL_TOKEN is empty: the API will reject the worker's messages")
        notifier = Notifier(evidence_dir=settings.evidence_dir, api_url=settings.api_url,
                            token=settings.internal_token, model_version=detector.model_version)
        notifier.start()
        disabled = disabled_cameras(ws)
        for c in sorted(disabled & set(readers)):
            log.info("cam%d is disabled in the database: it runs from the command line, but /stream/%d answers 404",
                     c, c)
        hub = FrameHub(readers, disabled=disabled)
        web = WebServer(hub, settings.internal_token, crossing_check=args.crossing_check)
        web.start()
        reloader = Reloader(load=load_settings, cli_sources=bool(args.source1 or args.source2), retry_s=args.retry,
                            crossing_check=args.crossing_check)

        Scheduler(readers, detector, pipelines, interval=args.interval, show=args.show,
                  display_width=args.display_width, report_every=args.report_every, duration=args.duration,
                  stop=stop, engines=engines, notifier=notifier, settings=ws, hub=hub,
                  reload_event=web.reload_event, reloader=reloader, crossing_check=args.crossing_check,
                  perf_csv=settings.logs_dir / "perf.csv").run()
    except Exception:
        log.exception("worker failed")
        return 1
    finally:
        if web is not None:
            web.stop()
        for r in readers.values():
            r.stop()
        if notifier is not None:  # clean stop: close open incidents, then let the notifier finish
            closes = [ev for e in engines.values() for ev in e.close_all(time.time())]
            for ev in closes:
                notifier.submit(CloseJob(ev))
            log.info("stopping: closing %d open incident(s), flushing the notifier", len(closes))
            notifier.stop(timeout=30)
            log.info("notifier: %s", dict(notifier.stats))
        join_end = time.monotonic() + 7
        for r in readers.values():
            r.join(timeout=max(0.0, join_end - time.monotonic()))
            if r.is_alive():
                log.warning("cam%d: reader did not stop in time (camera not released)", r.camera_id)
        if args.show:
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        log.info("worker stopped%s", f" {time.monotonic() - stop_at[0]:.1f} s after the stop signal" if stop_at else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
