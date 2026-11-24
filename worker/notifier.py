"""Notifier: everything slow – JPEG, database, HTTP – happens here, never in the main loop.

Two threads with bounded queues:
- notifier-db: incident open (incident row + evidence JPEG + evidence row in ONE transaction; a failed JPEG write
  rolls it all back), incident close (end_time, conf_avg, conf_max only), camera_health upsert, and at start the
  closing of incidents left open by a previous run. After each commit it queues the API message.
- notifier-http: POSTs messages 2 / 5 / 6 to RADS_API_URL with X-RADS-Internal. Messages 2 and 5 always go before
  any waiting heartbeat; API down -> they are retried 3 times with a 1 s back-off, logged, and the worker continues.
  Heartbeats (message 6) are sent once, never retried – the next one follows 5 s later. A dead API therefore never
  delays the database writes.
When a queue is full, heartbeats are dropped first (and a waiting heartbeat of the same camera is replaced by the
newer one); open/close events are never dropped – the queue grows past its bound and a warning is logged.
The worker writes only: incidents (insert; end_time, conf_avg, conf_max), evidence, camera_health.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from sqlalchemy import Engine, MetaData, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from shared.contract import (INTERNAL_HEADER, NOTIFY_RETRIES, PATH_HEARTBEAT, PATH_INCIDENT_CLOSED,
                             PATH_INCIDENT_NEW, TZ, CameraStatus, EvidenceKind, Heartbeat, IncidentClosed,
                             IncidentNew, IncidentStatus)
from worker.draw import draw_evidence
from worker.linking import Worker
from worker.rules import IncidentClose, IncidentOpen
from worker.settings import Zone

log = logging.getLogger(__name__)


def iso_time(t: float) -> str:
    """Epoch seconds -> '2026-12-02T14:05:31+05:00' (Asia/Karachi)."""
    return datetime.fromtimestamp(t, TZ).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- jobs


@dataclass
class OpenJob:
    event: IncidentOpen
    frame: np.ndarray            # a copy of the evidence frame (drawn on in place)
    captured_t: float            # epoch seconds of the frame
    detections: list[dict[str, Any]]
    workers: list[Worker]
    zones: list[Zone]            # the rule's zone(s), drawn on the evidence
    hazard_name: str


@dataclass
class CloseJob:
    event: IncidentClose


@dataclass
class HeartbeatJob:
    camera_id: int
    status: CameraStatus
    fps: float | None
    t: float                     # epoch seconds of the heartbeat
    last_seen_t: float | None    # epoch seconds of the camera's newest frame (None: never seen)


@dataclass
class _StartupCleanup:
    pass


@dataclass
class _Post:
    path: str
    body: dict[str, Any]
    what: str                    # for the log, e.g. "incident.new 1042"
    heartbeat: bool = False


class JobQueue:
    """FIFO with a soft bound: droppable items (heartbeats) are dropped first, others are never dropped.
    With priority=True, get() returns waiting non-droppable items (messages 2/5) before droppable ones."""

    def __init__(self, maxsize: int, name: str, priority: bool = False) -> None:
        self.maxsize = maxsize
        self.name = name
        self.priority = priority
        self._dq: deque[list] = deque()  # [droppable, coalesce_key, item]
        self._cv = threading.Condition()
        self.dropped = 0
        self.over_bound = 0
        self._last_warn = 0.0

    def __len__(self) -> int:
        with self._cv:
            return len(self._dq)

    def _warn(self, msg: str, *args) -> None:
        now = time.monotonic()
        if now - self._last_warn > 10.0:
            self._last_warn = now
            log.warning(msg, *args)

    def put(self, item: Any, droppable: bool = False, coalesce_key: Any = None) -> bool:
        with self._cv:
            if coalesce_key is not None:
                for entry in self._dq:
                    if entry[1] == coalesce_key:  # a newer heartbeat replaces the waiting one
                        entry[2] = item
                        self.dropped += 1
                        return True
            if len(self._dq) >= self.maxsize:
                if droppable:
                    self.dropped += 1
                    self._warn("notifier %s queue full (%d): dropping heartbeats", self.name, self.maxsize)
                    return False
                victim = next((e for e in self._dq if e[0]), None)
                if victim is not None:
                    self._dq.remove(victim)
                    self.dropped += 1
                else:
                    self.over_bound += 1
                    self._warn("notifier %s queue full (%d) of events: keeping them anyway (%d over)",
                               self.name, self.maxsize, len(self._dq) + 1 - self.maxsize)
            self._dq.append([droppable, coalesce_key, item])
            self._cv.notify()
            return True

    def get(self, timeout: float) -> Any:
        with self._cv:
            if not self._dq:
                self._cv.wait(timeout)
            if not self._dq:
                return None
            if self.priority:
                for entry in self._dq:
                    if not entry[0]:
                        self._dq.remove(entry)
                        return entry[2]
            return self._dq.popleft()[2]


# --------------------------------------------------------------------------- notifier


class Notifier:
    def __init__(self, *, evidence_dir: Path, api_url: str, token: str, model_version: str,
                 db_engine: Engine | None = None, http_client: httpx.Client | None = None, maxsize: int = 200,
                 retries: int = NOTIFY_RETRIES, backoff_s: float = 1.0, timeout_s: float = 2.0,
                 jpeg_quality: int = 85) -> None:
        if db_engine is None:
            from shared.db import get_engine
            db_engine = get_engine()
        self._db = db_engine
        md = MetaData()
        md.reflect(bind=self._db, only=["incidents", "evidence", "camera_health"])
        self._inc, self._ev, self._health = md.tables["incidents"], md.tables["evidence"], md.tables["camera_health"]
        self.evidence_dir = Path(evidence_dir)
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.model_version = model_version
        self.retries = retries
        self.backoff_s = backoff_s
        self.timeout_s = timeout_s
        self.jpeg_quality = jpeg_quality
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._own_http = http_client is None
        self.jobs = JobQueue(maxsize, "db")
        self.posts = JobQueue(maxsize, "http", priority=True)
        self._ids: dict[int, int] = {}        # rule-engine ref -> incidents.incident_id
        self._stopping = threading.Event()
        self._db_done = threading.Event()
        self._api_down = False
        self._unknown_cameras: set[int] = set()
        self.stats: Counter[str] = Counter()
        self._db_thread = threading.Thread(target=self._db_loop, name="notifier-db", daemon=True)
        self._http_thread = threading.Thread(target=self._http_loop, name="notifier-http", daemon=True)

    # ------------------------------------------------------------------ public (main thread)

    def start(self, close_stale: bool = True) -> None:
        if close_stale:
            self.jobs.put(_StartupCleanup())
        self._db_thread.start()
        self._http_thread.start()

    def submit(self, job: OpenJob | CloseJob | HeartbeatJob) -> bool:
        """Never blocks. Heartbeats may be dropped when the queue is full; open/close events never are."""
        if isinstance(job, HeartbeatJob):
            return self.jobs.put(job, droppable=True, coalesce_key=("hb", job.camera_id))
        return self.jobs.put(job)

    def stop(self, timeout: float = 30.0) -> bool:
        """Finish both queues, then stop the threads. Returns True if everything was handled in time."""
        self._stopping.set()
        deadline = time.monotonic() + timeout
        self._db_thread.join(max(0.0, deadline - time.monotonic()))
        self._http_thread.join(max(0.0, deadline - time.monotonic()))
        done = not self._db_thread.is_alive() and not self._http_thread.is_alive()
        if not done:
            log.warning("notifier stop timed out: %d jobs and %d messages left", len(self.jobs), len(self.posts))
        elif self._own_http:
            self._http.close()
        return done

    # ------------------------------------------------------------------ threads

    def _db_loop(self) -> None:
        while True:
            job = self.jobs.get(timeout=0.2)
            if job is None:
                if self._stopping.is_set():
                    break
                continue
            try:
                if isinstance(job, OpenJob):
                    self._open(job)
                elif isinstance(job, CloseJob):
                    self._close(job)
                elif isinstance(job, HeartbeatJob):
                    self._heartbeat(job)
                elif isinstance(job, _StartupCleanup):
                    self._close_stale()
            except Exception:
                self.stats["db_errors"] += 1
                log.exception("notifier: %s failed", type(job).__name__)
        self._db_done.set()

    def _http_loop(self) -> None:
        while True:
            post = self.posts.get(timeout=0.2)
            if post is None:
                if self._db_done.is_set():
                    break
                continue
            self._send(post)

    # ------------------------------------------------------------------ database jobs

    def _open(self, job: OpenJob) -> None:
        ev = job.event
        who = (f"track {ev.track_id}" if ev.track_id is not None
               else f"phone spot {ev.spot_id}" if ev.spot_id is not None else "")
        title = [f"{ev.hazard_id} {job.hazard_name} | cam{ev.camera_id} zone {ev.zone_id} {who}",
                 f"start {iso_time(ev.start_t)}  snapshot {iso_time(job.captured_t)}",
                 f"conf avg {ev.conf_avg:.2f} max {ev.conf_max:.2f}  {ev.rule.severity}  model {self.model_version}"]
        img = draw_evidence(job.frame, job.zones, job.detections, job.workers, ev.trigger, ev.subject_box, title)
        detections = json.dumps({
            "hazard_id": ev.hazard_id, "track_id": ev.track_id, "spot_id": ev.spot_id,
            "trigger": _det(ev.trigger) if ev.trigger else None,
            "subject_box": [round(v, 1) for v in ev.subject_box] if ev.subject_box else None,
            "detections": [_det(d) for d in job.detections],
            "workers": [{"track_id": w.track_id, "box": [round(v, 1) for v in w.box], "head": w.head,
                         "mask": w.mask, "apron": w.apron, "zone_ids": list(w.zone_ids),
                         "confs": {k: round(v, 3) for k, v in w.confs.items()}} for w in job.workers],
        })
        day = datetime.fromtimestamp(job.captured_t, TZ).strftime("%Y-%m-%d")
        path: Path | None = None
        try:
            with self._db.begin() as con:
                res = con.execute(self._inc.insert().values(
                    hazard_id=ev.hazard_id, rule_id=ev.rule.rule_id, camera_id=ev.camera_id, zone_id=ev.zone_id,
                    track_id=ev.track_id, start_time=iso_time(ev.start_t), end_time=None,
                    conf_avg=round(ev.conf_avg, 4), conf_max=round(ev.conf_max, 4), severity=ev.rule.severity,
                    model_version=self.model_version, status=IncidentStatus.NEW.value))
                incident_id = int(res.inserted_primary_key[0])
                rel = f"{day}/cam{ev.camera_id}/{incident_id}.jpg"
                path = self.evidence_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]):
                    raise OSError(f"could not write {path}")
                con.execute(self._ev.insert().values(
                    incident_id=incident_id, kind=EvidenceKind.SNAPSHOT.value, file_path=rel,
                    captured_at=iso_time(job.captured_t), detections=detections))
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            self.stats["open_failed"] += 1
            log.exception("cam%d %s: storing the incident failed – rolled back, nothing written",
                          ev.camera_id, ev.hazard_id)
            return
        self._ids[ev.ref] = incident_id
        self.stats["opened"] += 1
        log.info("incident %d stored: cam%d %s zone %d %s start %s evidence %s", incident_id, ev.camera_id,
                 ev.hazard_id, ev.zone_id, who, iso_time(ev.start_t), rel)
        self.posts.put(_Post(PATH_INCIDENT_NEW, IncidentNew(incident_id=incident_id).model_dump(),
                             f"incident.new {incident_id}"))

    def _close(self, job: CloseJob) -> None:
        ev = job.event
        incident_id = self._ids.pop(ev.ref, None)
        if incident_id is None:
            log.warning("cam%d %s: close for an incident that was never stored (ref %d) – skipped",
                        ev.camera_id, ev.hazard_id, ev.ref)
            return
        with self._db.begin() as con:
            con.execute(update(self._inc)
                        .where(self._inc.c.incident_id == incident_id, self._inc.c.end_time.is_(None))
                        .values(end_time=iso_time(ev.end_t), conf_avg=round(ev.conf_avg, 4),
                                conf_max=round(ev.conf_max, 4)))
        self.stats["closed"] += 1
        log.info("incident %d closed (%s): end %s conf avg %.2f max %.2f", incident_id, ev.reason,
                 iso_time(ev.end_t), ev.conf_avg, ev.conf_max)
        self.posts.put(_Post(PATH_INCIDENT_CLOSED, IncidentClosed(incident_id=incident_id).model_dump(),
                             f"incident.closed {incident_id}"))

    def _heartbeat(self, job: HeartbeatJob) -> None:
        values = {"camera_id": job.camera_id, "status": job.status.value, "fps": job.fps,
                  "last_seen": iso_time(job.last_seen_t if job.last_seen_t is not None else job.t)}
        set_ = {"status": job.status.value, "fps": job.fps}
        if job.last_seen_t is not None:
            set_["last_seen"] = values["last_seen"]
        stmt = sqlite_insert(self._health).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[self._health.c.camera_id], set_=set_)
        try:
            with self._db.begin() as con:
                con.execute(stmt)
        except IntegrityError:  # a camera given on the command line that is not in the cameras table
            if job.camera_id not in self._unknown_cameras:
                self._unknown_cameras.add(job.camera_id)
                log.warning("camera %d is not in the cameras table: no camera_health row", job.camera_id)
        self.stats["heartbeats"] += 1
        msg = Heartbeat(camera_id=job.camera_id, status=job.status,
                        fps=None if job.fps is None else round(job.fps, 2), ts=iso_time(job.t))
        self.posts.put(_Post(PATH_HEARTBEAT, msg.model_dump(mode="json"), f"heartbeat cam{job.camera_id}", True),
                       droppable=True, coalesce_key=("hb", job.camera_id))

    def _close_stale(self) -> None:
        """Incidents left open by a previous run (crash, kill): end_time = start_time, then message 5."""
        with self._db.begin() as con:
            ids = [r.incident_id for r in con.execute(
                select(self._inc.c.incident_id).where(self._inc.c.end_time.is_(None)))]
            if ids:
                con.execute(update(self._inc).where(self._inc.c.end_time.is_(None))
                            .values(end_time=self._inc.c.start_time))
        if ids:
            log.warning("closed %d incident(s) left open by a previous run (end_time = start_time): %s",
                        len(ids), ids)
        for incident_id in ids:
            self.posts.put(_Post(PATH_INCIDENT_CLOSED, IncidentClosed(incident_id=incident_id).model_dump(),
                                 f"incident.closed {incident_id} (previous run)"))

    # ------------------------------------------------------------------ HTTP

    def _send(self, post: _Post) -> bool:
        attempts = 1 if post.heartbeat else 1 + self.retries  # heartbeats: no retry, the next one comes in 5 s
        level = logging.WARNING
        for attempt in range(1, attempts + 1):
            try:
                r = self._http.post(self.api_url + post.path, json=post.body,
                                    headers={INTERNAL_HEADER: self.token}, timeout=self.timeout_s)
                if r.status_code < 300:
                    if self._api_down:
                        self._api_down = False
                        log.info("API %s reachable again", self.api_url)
                    self.stats["sent"] += 1
                    return True
                if r.status_code < 500:  # 401 / 422: retrying will not help
                    self.stats["rejected"] += 1
                    log.error("%s rejected by the API: HTTP %d %s", post.what, r.status_code, r.text[:200])
                    return False
                err = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}: {e}"
            if not self._api_down:
                self._api_down = True
                log.warning("API %s unreachable (%s)", self.api_url, err)
            if attempt < attempts:
                self.stats["retries"] += 1
                log.log(level, "%s: attempt %d/%d failed (%s), retrying in %.0f s", post.what, attempt, attempts,
                        err, self.backoff_s)
                time.sleep(self.backoff_s)
        if post.heartbeat:
            self.stats["heartbeats_failed"] += 1
            log.debug("%s: API down, not retried", post.what)
            return False
        self.stats["gave_up"] += 1
        log.warning("%s: API down, gave up after %d attempts", post.what, attempts)
        return False


def _det(d: dict[str, Any]) -> dict[str, Any]:
    return {"cls_name": d["cls_name"], "conf": round(float(d["conf"]), 3), "xyxy": [round(float(v), 1) for v in d["xyxy"]]}
