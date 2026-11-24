"""Fake pipeline worker for testing the app server without cameras or a model (Member B).

Behaves like the real worker (contract sections 3, 5, 6):
  * every --interval s: inserts an incident + evidence row, writes a placeholder JPEG to
    rads_data/evidence/YYYY-MM-DD/cam<id>/<incident_id>.jpg, then POSTs /internal/incidents/new
  * after a random duration: sets end_time / conf_avg / conf_max, then POSTs /internal/incidents/closed
  * every 5 s per camera: upserts camera_health, then POSTs /internal/heartbeat
Only writes what the worker owns: incidents (insert, end_time, conf_*), evidence, camera_health.
Rows are tagged model_version='fake_incidents'; remove them with --purge.

It also stands in for the worker's own HTTP server (contract messages 7 and 8) on RADS_WORKER_URL (port 8001):
  * POST /reload           -> {"ok": true}   (rules and zones are re-read anyway before every fake incident)
  * GET  /stream/{camera}  -> MJPEG at ~2 FPS: a test pattern (or --video FILE) with the camera's zones drawn,
                              re-read from the DB every frame, so zone edits show up at once
Both require X-RADS-Internal, like the real worker.

    python scripts/fake_incidents.py                    # run until Ctrl+C
    python scripts/fake_incidents.py --interval 10 --count 3
    python scripts/fake_incidents.py --no-incidents     # heartbeats + /reload + /stream only
    python scripts/fake_incidents.py --video clip.mp4   # stream a video file (needs opencv-python)
    python scripts/fake_incidents.py --purge            # delete all fake incidents + their files (asks first)
    python scripts/fake_incidents.py --backfill 200 --days 45   # 200 closed incidents spread over 45 past days,
                                                    # some reviewed (test data for reports and retention), then exit
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import random
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/fake_incidents.py`

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from shared.config import get_settings  # noqa: E402
from shared.db import get_engine  # noqa: E402
from shared.contract import (HEARTBEAT_INTERVAL_S, INTERNAL_HEADER, NOTIFY_RETRIES, PATH_HEARTBEAT,  # noqa: E402
                             PATH_INCIDENT_CLOSED, PATH_INCIDENT_NEW, PATH_WORKER_RELOAD, PATH_WORKER_STREAM,
                             STREAM_FPS, TZ, CameraStatus, Heartbeat, IncidentRef, ReloadResponse, TiedTo, now_iso)

MODEL_VERSION = "fake_incidents"
log = logging.getLogger("fake_incidents")


# ---------------------------------------------------------------- messages to the app

class Notifier:
    """POSTs contract messages to the app. Never raises: retries NOTIFY_RETRIES times, then logs and gives up
    (the row is already in the DB, so the dashboard still shows it on refresh)."""

    def __init__(self, client: httpx.Client, token: str, retry_delay: float = 1.0):
        self.client = client
        self.token = token
        self.retry_delay = retry_delay

    @classmethod
    def for_url(cls, api_url: str, token: str) -> Notifier:
        return cls(httpx.Client(base_url=api_url, timeout=3.0), token)

    def post(self, path: str, body: dict[str, Any], retries: int = NOTIFY_RETRIES) -> bool:
        for attempt in range(1, retries + 1):
            try:
                r = self.client.post(path, json=body, headers={INTERNAL_HEADER: self.token})
            except httpx.HTTPError as e:
                log.warning("POST %s failed (attempt %d/%d): %s", path, attempt, retries, e)
            else:
                if r.is_success:
                    return True
                if 400 <= r.status_code < 500:  # retrying will not fix a bad token or bad body
                    log.error("POST %s rejected: %s %s", path, r.status_code, r.text[:200])
                    return False
                log.warning("POST %s -> %s (attempt %d/%d)", path, r.status_code, attempt, retries)
            if attempt < retries:
                time.sleep(self.retry_delay)
        log.error("POST %s gave up after %d attempts", path, retries)
        return False

    def heartbeat(self, body: dict[str, Any]) -> bool:
        return self.post(PATH_HEARTBEAT, body, retries=1)  # the next one comes in 5 s anyway


# ---------------------------------------------------------------- placeholder evidence image

def placeholder_jpeg(path: Path, lines: list[str], box: tuple[int, int, int, int]) -> None:
    img = Image.new("RGB", (1280, 720), (38, 42, 51))
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, outline=(230, 57, 70), width=6)
    try:
        font = ImageFont.load_default(size=40)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((40, y), line, fill=(240, 240, 240), font=font)
        y += 56
    draw.text((40, 650), "FAKE EVIDENCE - scripts/fake_incidents.py", fill=(150, 150, 150), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=80)


# ---------------------------------------------------------------- the fake worker

class FakeWorker:
    def __init__(self, Session_: sessionmaker[Session], evidence_dir: Path, notifier: Notifier | None,
                 rng: random.Random | None = None, min_duration: float = 8, max_duration: float = 30,
                 offline_camera: int | None = None, clock: Callable[[], float] = time.monotonic):
        self.Session = Session_
        self.evidence_dir = evidence_dir
        self.notifier = notifier
        self.rng = rng or random.Random()
        self.min_duration = min_duration
        self.max_duration = max_duration
        self.offline_camera = offline_camera
        self.clock = clock
        self.open: dict[int, tuple[float, list[float]]] = {}  # incident_id -> (close at, confidences)

    # ---- helpers
    def _notify(self, path: str, incident_id: int) -> None:
        if self.notifier:
            self.notifier.post(path, IncidentRef(incident_id=incident_id).model_dump())

    def rules(self) -> list[dict[str, Any]]:
        """Enabled rules on enabled cameras that have at least one active zone (re-read each time, like reload)."""
        with self.Session() as s:
            rows = s.execute(text("""
                SELECT r.rule_id, r.hazard_id, r.camera_id, r.conf_threshold, r.severity,
                       h.name AS hazard_name, h.violation_class, h.tied_to, c.name AS camera_name,
                       GROUP_CONCAT(z.zone_id) AS zone_ids
                FROM rules r
                JOIN hazards h ON h.hazard_id = r.hazard_id
                JOIN cameras c ON c.camera_id = r.camera_id AND c.enabled = 1
                JOIN rule_zones rz ON rz.rule_id = r.rule_id
                JOIN zones z ON z.zone_id = rz.zone_id AND z.active = 1
                WHERE r.enabled = 1
                GROUP BY r.rule_id ORDER BY r.rule_id""")).mappings().all()
        return [{**r, "zone_ids": [int(z) for z in r["zone_ids"].split(",")]} for r in rows]

    # ---- message 2
    def new_incident(self, rule: dict[str, Any] | None = None, start: datetime | None = None) -> int:
        """Open a new incident now (message 2 follows). With `start` (backfill), only write the rows."""
        backfill = start is not None
        if rule is None:
            rules = self.rules()
            if not rules:
                raise RuntimeError("No enabled rules with zones; run scripts/seed.py")
            rule = self.rng.choice(rules)
        rng = self.rng
        zone_id = rng.choice(rule["zone_ids"])
        track_id = rng.randint(1, 60) if rule["tied_to"] == TiedTo.WORKER else None
        conf = round(rng.uniform(max(rule["conf_threshold"], 0.5), 0.95), 3)
        start = start or datetime.now(TZ)
        start_iso = start.isoformat(timespec="seconds")
        x, y = rng.randint(200, 800), rng.randint(150, 350)
        box = (x, y, x + rng.randint(120, 300), y + rng.randint(150, 300))

        with self.Session() as s:
            incident_id = s.execute(text(
                "INSERT INTO incidents (hazard_id, rule_id, camera_id, zone_id, track_id, start_time, conf_avg, "
                "conf_max, severity, model_version) VALUES (:h, :r, :c, :z, :t, :st, :ca, :cm, :sev, :mv) "
                "RETURNING incident_id"),
                {"h": rule["hazard_id"], "r": rule["rule_id"], "c": rule["camera_id"], "z": zone_id,
                 "t": track_id, "st": start_iso, "ca": conf, "cm": conf, "sev": rule["severity"],
                 "mv": MODEL_VERSION}).scalar_one()

            rel = f"{start:%Y-%m-%d}/cam{rule['camera_id']}/{incident_id}.jpg"
            placeholder_jpeg(self.evidence_dir / rel, [
                f"{rule['hazard_id']}  {rule['hazard_name']}",
                f"{rule['camera_name']}  (zone {zone_id})",
                f"incident {incident_id}" + (f"  track {track_id}" if track_id else ""),
                start_iso,
            ], box)
            detections = [{"class": rule["violation_class"], "conf": conf, "box": list(box), "track_id": track_id}]
            s.execute(text("INSERT INTO evidence (incident_id, kind, file_path, captured_at, detections) "
                           "VALUES (:i, 'snapshot', :p, :t, :d)"),
                      {"i": incident_id, "p": rel, "t": start_iso, "d": json.dumps(detections)})
            s.commit()

        if backfill:
            return incident_id
        self.open[incident_id] = (self.clock() + rng.uniform(self.min_duration, self.max_duration), [conf])
        log.info("NEW    incident %d  %s %s  cam%d  zone %d", incident_id, rule["hazard_id"],
                 rule["hazard_name"], rule["camera_id"], zone_id)
        self._notify(PATH_INCIDENT_NEW, incident_id)
        return incident_id

    # ---- test data: past incidents
    REVIEW_MIX = (("New", 25), ("Acknowledged", 15), ("Confirmed violation", 40), ("False alarm", 20))

    def backfill(self, count: int, days: int, now: datetime | None = None) -> list[int]:
        """`count` closed incidents at random times over the last `days` days, with evidence in the matching
        (old) day folders. As TEST DATA they also get a random review status (which the real worker never
        writes), and those left New are marked escalated, as the app would have done. No messages are sent."""
        now = (now or datetime.now(TZ)).astimezone(TZ)
        rules = self.rules()
        if not rules:
            raise RuntimeError("No enabled rules with zones; run scripts/seed.py")
        with self.Session() as s:
            reviewer = s.execute(text(
                "SELECT u.user_id FROM users u JOIN roles r USING (role_id) WHERE u.active = 1 "
                "AND r.name IN ('Manager', 'Admin') ORDER BY r.name = 'Admin', u.user_id")).scalar()
        statuses, weights = zip(*self.REVIEW_MIX)
        ids = []
        span = max(days * 86400 - 900, 1)  # newest one at least 15 min old
        for start in sorted(now - timedelta(seconds=900 + self.rng.uniform(0, span)) for _ in range(count)):
            start = start.replace(microsecond=0)
            iid = self.new_incident(self.rng.choice(rules), start=start)
            end = start + timedelta(seconds=self.rng.randint(8, 180))
            confs = [round(self.rng.uniform(0.45, 0.95), 3) for _ in range(self.rng.randint(3, 12))]
            status = self.rng.choices(statuses, weights)[0]
            review: dict[str, Any] = {"st": status, "by": None, "at": None, "esc": None}
            if status == "New":
                review["esc"] = (start + timedelta(minutes=10)).isoformat(timespec="seconds")
            elif reviewer is not None:
                review["by"] = reviewer
                review["at"] = (start + timedelta(minutes=self.rng.randint(1, 30))).isoformat(timespec="seconds")
            with self.Session() as s:
                s.execute(text("UPDATE incidents SET end_time = :e, conf_avg = :a, conf_max = :m, status = :st, "
                               "reviewed_by = :by, reviewed_at = :at, escalated_at = :esc WHERE incident_id = :i"),
                          {"e": end.isoformat(timespec="seconds"), "a": round(sum(confs) / len(confs), 3),
                           "m": max(confs), "i": iid, **review})
                s.commit()
            ids.append(iid)
        return ids

    # ---- message 5
    def close_incident(self, incident_id: int) -> None:
        _, confs = self.open.pop(incident_id)
        confs += [round(self.rng.uniform(0.45, 0.95), 3) for _ in range(self.rng.randint(3, 12))]
        with self.Session() as s:
            s.execute(text("UPDATE incidents SET end_time = :e, conf_avg = :a, conf_max = :m "
                           "WHERE incident_id = :i"),
                      {"e": now_iso(), "a": round(sum(confs) / len(confs), 3), "m": max(confs), "i": incident_id})
            s.commit()
        log.info("CLOSED incident %d", incident_id)
        self._notify(PATH_INCIDENT_CLOSED, incident_id)

    def close_due(self) -> None:
        now = self.clock()
        for incident_id in [i for i, (due, _) in self.open.items() if due <= now]:
            self.close_incident(incident_id)

    def close_all(self) -> None:
        for incident_id in list(self.open):
            self.close_incident(incident_id)

    # ---- message 6
    def heartbeats(self) -> None:
        with self.Session() as s:
            cams = [r[0] for r in s.execute(text("SELECT camera_id FROM cameras WHERE enabled = 1 ORDER BY camera_id"))]
            beats = []
            for cam in cams:
                if cam == self.offline_camera:
                    continue  # simulate a dead camera: no heartbeat, camera_health goes stale
                hb = Heartbeat(camera_id=cam, status=CameraStatus.ONLINE,
                               fps=round(self.rng.uniform(4.8, 5.0), 1), ts=now_iso())
                s.execute(text("INSERT INTO camera_health (camera_id, status, fps, last_seen) VALUES (:c, :s, :f, :t) "
                               "ON CONFLICT(camera_id) DO UPDATE SET status = excluded.status, fps = excluded.fps, "
                               "last_seen = excluded.last_seen"),
                          {"c": cam, "s": hb.status.value, "f": hb.fps, "t": hb.ts})
                beats.append(hb)
            s.commit()
        if self.notifier:
            for hb in beats:
                self.notifier.heartbeat(hb.model_dump(mode="json"))


# ---------------------------------------------------------------- cleanup

def purge(Session_: sessionmaker[Session], evidence_dir: Path) -> int:
    """Delete every fake incident with its evidence rows, alerts and files. Returns the number of incidents."""
    with Session_() as s:
        ids = [r[0] for r in s.execute(text("SELECT incident_id FROM incidents WHERE model_version = :mv"),
                                       {"mv": MODEL_VERSION})]
        if not ids:
            return 0
        sub = "SELECT incident_id FROM incidents WHERE model_version = :mv"
        files = [r[0] for r in s.execute(text(f"SELECT file_path FROM evidence WHERE incident_id IN ({sub})"),
                                         {"mv": MODEL_VERSION})]
        s.execute(text(f"DELETE FROM alerts WHERE incident_id IN ({sub})"), {"mv": MODEL_VERSION})
        s.execute(text(f"DELETE FROM evidence WHERE incident_id IN ({sub})"), {"mv": MODEL_VERSION})
        s.execute(text("DELETE FROM incidents WHERE model_version = :mv"), {"mv": MODEL_VERSION})
        s.commit()
    base = evidence_dir.resolve()
    for rel in files:
        p = (base / rel).resolve()
        if p.is_relative_to(base):
            p.unlink(missing_ok=True)
            for d in (p.parent, p.parent.parent):  # remove cam<id>/ and the day folder if now empty
                if d != base and d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
    return len(ids)


# ---------------------------------------------------------------- worker HTTP stand-in (messages 7 and 8)

ZONE_COLOURS = {"prep": (46, 204, 113), "waste": (243, 156, 18), "ignore": (149, 165, 166)}
STREAM_WIDTH = 960  # frames are drawn in camera pixels, then scaled down to this width


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class VideoSource:
    """Frames from a video file, looping (needs opencv-python)."""

    def __init__(self, path: str):
        try:
            import cv2  # noqa: PLC0415
        except ImportError:
            raise SystemExit("--video needs OpenCV: pip install opencv-python") from None
        self.cv2, self.path = cv2, path
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise SystemExit(f"Cannot open video {path}")
        self.lock = threading.Lock()

    def frame(self) -> Image.Image:
        with self.lock:
            ok, bgr = self.cap.read()
            if not ok:  # end of file: start again
                self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
                ok, bgr = self.cap.read()
        if not ok:
            raise RuntimeError(f"cannot read frames from {self.path}")
        return Image.fromarray(self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB))


def render_frame(camera: dict[str, Any], zones: list[dict[str, Any]], n: int,
                 video: VideoSource | None = None) -> bytes:
    """One JPEG: test pattern (or video frame) at the camera's size, zones drawn, scaled to STREAM_WIDTH."""
    w, h = camera["frame_width"] or 1920, camera["frame_height"] or 1080
    if video is not None:
        img = video.frame().resize((w, h))
    else:
        img = Image.new("RGB", (w, h), (30, 34, 42))
        bars = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0), (0, 0, 192)]
        bw = w // len(bars)
        d = ImageDraw.Draw(img)
        for i, c in enumerate(bars):
            d.rectangle((i * bw, 0, (i + 1) * bw, h // 8), fill=c)
        x = (n * w // 40) % w  # moving line: proves the picture is live
        d.rectangle((x, h // 8, x + w // 100, h), fill=(80, 90, 110))
    d = ImageDraw.Draw(img)
    big, small = _font(max(h // 22, 12)), _font(max(h // 36, 10))
    for z in zones:
        pts = [tuple(p) for p in json.loads(z["polygon"])]
        colour = ZONE_COLOURS.get(z["type"], (255, 255, 255)) if z["active"] else (90, 90, 90)
        d.line(pts + [pts[0]], fill=colour, width=max(w // 320, 2))
        d.text((pts[0][0] + 10, pts[0][1] + 10), f"{z['name']} ({z['type']})", fill=colour, font=small)
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    d.text((w // 40, h // 8 + h // 40), f"{camera['name']}  |  {stamp}  |  frame {n}", fill=(255, 255, 255), font=big)
    label = "FAKE WORKER - test pattern (scripts/fake_incidents.py)" if video is None else "FAKE WORKER - video file"
    d.text((w // 40, h - h // 12), label, fill=(200, 200, 200), font=small)
    img = img.resize((STREAM_WIDTH, round(h * STREAM_WIDTH / w)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def create_worker_app(Session_: sessionmaker[Session], token: str, offline_camera: int | None = None,
                      video: VideoSource | None = None, fps: float = STREAM_FPS,
                      max_frames: int | None = None) -> FastAPI:
    """The worker's HTTP endpoints, faked. `max_frames` ends each stream early (tests)."""
    api = FastAPI(title="RADS fake worker")
    api.state.reloads = 0

    def check(x_rads_internal: str | None) -> None:
        if not token or x_rads_internal != token:
            raise HTTPException(401, "Missing or wrong internal token")

    @api.post(PATH_WORKER_RELOAD, response_model=ReloadResponse)
    def reload(x_rads_internal: str | None = Header(None, alias=INTERNAL_HEADER)) -> ReloadResponse:
        check(x_rads_internal)
        api.state.reloads += 1
        log.info("RELOAD requested by the app (settings changed) - #%d", api.state.reloads)
        return ReloadResponse(ok=True)

    @api.get(PATH_WORKER_STREAM)
    def stream(camera_id: int, x_rads_internal: str | None = Header(None, alias=INTERNAL_HEADER)):
        check(x_rads_internal)
        if camera_id == offline_camera:
            raise HTTPException(503, f"camera {camera_id} is offline")
        with Session_() as s:
            cam = s.execute(text("SELECT * FROM cameras WHERE camera_id = :c AND enabled = 1"),
                            {"c": camera_id}).mappings().first()
        if cam is None:
            raise HTTPException(404, f"no enabled camera {camera_id}")
        cam = dict(cam)

        async def frames():
            n = 0
            while max_frames is None or n < max_frames:
                with Session_() as s:  # zones re-read every frame: edits show up at once
                    zones = [dict(z) for z in s.execute(
                        text("SELECT * FROM zones WHERE camera_id = :c ORDER BY zone_id"), {"c": camera_id}).mappings()]
                jpeg = await asyncio.to_thread(render_frame, cam, zones, n, video)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                       + b"\r\n\r\n" + jpeg + b"\r\n")
                n += 1
                await asyncio.sleep(1 / fps)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    return api


def start_worker_server(api: FastAPI, worker_url: str) -> uvicorn.Server | None:
    """Serve the stand-in in a background thread on RADS_WORKER_URL's host and port."""
    u = httpx.URL(worker_url)
    server = uvicorn.Server(uvicorn.Config(api, host=u.host, port=u.port or 8001, log_level="warning"))
    thread = threading.Thread(target=server.run, name="fake-worker-http", daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            log.info("Worker stand-in listening on %s (/reload, /stream/{camera_id})", worker_url)
            return server
        if not thread.is_alive():
            break
        time.sleep(0.1)
    log.warning("Could not start the worker stand-in on %s (port in use?); continuing without it", worker_url)
    return None


# ---------------------------------------------------------------- main loop

def run(worker: FakeWorker, interval: float, count: int | None, tick: float = 0.5,
        make_incidents: bool = True) -> None:
    created = 0
    next_new = next_hb = worker.clock()
    try:
        while True:
            now = worker.clock()
            if now >= next_hb:
                worker.heartbeats()
                next_hb = now + HEARTBEAT_INTERVAL_S
            if make_incidents and (count is None or created < count) and now >= next_new:
                worker.new_incident()
                created += 1
                next_new = now + interval
            worker.close_due()
            if count is not None and created >= count and not worker.open:
                log.info("Done: %d incidents created and closed.", created)
                return
            time.sleep(tick)
    except KeyboardInterrupt:
        log.info("Stopping; closing %d open incident(s)...", len(worker.open))
        worker.close_all()


def main() -> int:
    p = argparse.ArgumentParser(description="Fake RADS worker: writes fake incidents and calls the app.")
    p.add_argument("--interval", type=float, default=20, help="seconds between new incidents (default 20)")
    p.add_argument("--min-duration", type=float, default=8, help="shortest incident, seconds (default 8)")
    p.add_argument("--max-duration", type=float, default=30, help="longest incident, seconds (default 30)")
    p.add_argument("--count", type=int, default=None, help="stop after N incidents (default: run until Ctrl+C)")
    p.add_argument("--offline-camera", type=int, default=None, help="send no heartbeats for this camera id")
    p.add_argument("--no-api", action="store_true", help="only write to the DB, do not call the app")
    p.add_argument("--seed", type=int, default=None, help="random seed for repeatable runs")
    p.add_argument("--purge", action="store_true", help="delete all fake incidents and their files, then exit")
    p.add_argument("--yes", action="store_true", help="with --purge: do not ask for confirmation")
    p.add_argument("--no-incidents", action="store_true",
                   help="no fake incidents: only heartbeats and the /reload + /stream stand-in")
    p.add_argument("--no-server", action="store_true", help="do not start the /reload + /stream stand-in")
    p.add_argument("--video", default=None, help="stream this video file instead of a test pattern")
    p.add_argument("--backfill", type=int, default=None, metavar="N",
                   help="create N closed past incidents (test data for reports/retention), then exit")
    p.add_argument("--days", type=int, default=30, help="with --backfill: spread them over this many past days")
    args = p.parse_args()
    if args.min_duration > args.max_duration:
        p.error("--min-duration must not exceed --max-duration")
    if args.backfill is not None and (args.backfill < 1 or args.days < 1):
        p.error("--backfill and --days must be at least 1")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = get_settings()
    if not s.db_path.exists():
        print(f"No database at {s.db_path}. Run scripts/init_db.py and scripts/seed.py first.")
        return 1

    Session_ = sessionmaker(bind=get_engine(), expire_on_commit=False)

    if args.purge:
        with Session_() as db:
            n = db.execute(text("SELECT COUNT(*) FROM incidents WHERE model_version = :mv"),
                           {"mv": MODEL_VERSION}).scalar_one()
        if n == 0:
            print("No fake incidents to delete.")
            return 0
        if not args.yes and input(f"Delete ALL {n} fake incident(s) in {s.db_path}, with their evidence files "
                                  "and alerts? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return 0
        print(f"Deleted {purge(Session_, s.evidence_dir)} fake incident(s).")
        return 0

    if args.backfill:
        w = FakeWorker(Session_, s.evidence_dir, None, random.Random(args.seed))
        ids = w.backfill(args.backfill, args.days)
        with Session_() as db:
            first, last = db.execute(text("SELECT MIN(start_time), MAX(start_time) FROM incidents "
                                          f"WHERE incident_id IN ({', '.join(map(str, ids))})")).one()
        print(f"Created {len(ids)} past fake incidents ({ids[0]}-{ids[-1]}), {first[:10]} to {last[:10]}.")
        return 0

    notifier = None
    if not args.no_api:
        if not s.internal_token:
            print("RADS_INTERNAL_TOKEN is empty in .env; set it (same value the app uses) or use --no-api.")
            return 1
        notifier = Notifier.for_url(s.api_url, s.internal_token)
        log.info("Calling the app at %s", s.api_url)

    worker = FakeWorker(Session_, s.evidence_dir, notifier, random.Random(args.seed),
                        args.min_duration, args.max_duration, args.offline_camera)
    if not args.no_incidents and not worker.rules():
        print("No enabled rules with zones in the database. Run scripts/seed.py first.")
        return 1
    if not args.no_server:
        video = VideoSource(args.video) if args.video else None
        start_worker_server(create_worker_app(Session_, s.internal_token, args.offline_camera, video), s.worker_url)
    if args.no_incidents:
        log.info("Fake worker started: heartbeats only, no incidents. Ctrl+C to stop.")
    else:
        log.info("Fake worker started: new incident every %ss. Ctrl+C to stop.", args.interval)
    run(worker, args.interval, args.count, make_incidents=not args.no_incidents)
    return 0


if __name__ == "__main__":
    sys.exit(main())
