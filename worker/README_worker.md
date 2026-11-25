# RADS pipeline worker

The worker watches the two kitchen cameras, runs the YOLO model on each, follows every worker with a tracker,
applies the detection rules and records incidents (database rows + an evidence picture). It tells the app server
about each incident and sends a heartbeat per camera every 5 s. A small local web server gives the app a live view,
a "reload settings" call and a health page.

```
camera (RTSP) ─► reader thread ─► newest frame ─► main loop, 5 FPS per camera:
                                                  detect ─► track ─► link head/mask/apron to the person ─► zones ─► rules
                                                  │
                                                  ├─► notifier: DB rows + evidence JPEG, then message 2/5 to the app
                                                  └─► web server (127.0.0.1:8001): /stream, /reload, /health
```

## 1. Setup

1. **Python 3.12 virtual environment** in the repository root (`.venv`). Install PyTorch first (the build for this
   machine: Apple silicon uses MPS), then `pip install -r requirements.txt`. Always run the worker with
   `.venv/bin/python`.
2. **Model:** `models/v2_960_best.pt` (or set `RADS_MODEL_PATH`).
3. **Database:** created and seeded once with `scripts/init_db.py` and `scripts/seed.py` (see the main README). The
   worker reads cameras, zones, hazards and rules from it, and writes incidents, evidence rows and camera health.
4. **`.env`** (shared with the app server). The worker uses:

| Key | Meaning |
|---|---|
| `RADS_DATA_DIR` | data folder (default `./rads_data`): `evidence/`, `logs/` |
| `RADS_DB_PATH` | the SQLite database (default `./rads_data/rads.db`) |
| `RADS_API_URL` | the app server (default `http://127.0.0.1:8000`) for messages 2, 5 and 6 |
| `RADS_INTERNAL_TOKEN` | must be **exactly** the app server's value (header `X-RADS-Internal`) |
| `RADS_MODEL_PATH`, `RADS_IMGSZ` | model file and input size (960) |
| `RADS_DEVICE` | `mps` on the Mac (`auto`, `cuda`, `cpu` also work) |
| `CAM1_RTSP`, `CAM2_RTSP` | copied into the `cameras` table by the seed step; afterwards the DB value is used |

The cameras' picture size (`cameras.frame_width` × `frame_height`, 2688 × 1520) must match what the cameras send:
zones are stored in those pixels. The worker logs the real size and FPS of each source when it connects and warns
if the size differs from the DB.

## 2. Running

```bash
# the real cameras (each enabled camera's rtsp_url from the DB)
.venv/bin/python -m worker.main

# video files instead (replayed in real time, looped)
.venv/bin/python -m worker.main --source1 ~/Desktop/RADS_Edge_M4/clips/20261109230051844.mp4 \
                                --source2 ~/Desktop/RADS_Edge_M4/clips/20261109231027407.mp4

# any RTSP URL, e.g. the fake cameras below
.venv/bin/python -m worker.main --source1 rtsp://127.0.0.1:8554/cam1 --source2 rtsp://127.0.0.1:8554/cam2
```

| Flag | Default | Meaning |
|---|---|---|
| `--source1`, `--source2` | DB `rtsp_url` | a file or RTSP URL for camera 1 / 2. With any `--sourceN` only those cameras run, and a reload never restarts them |
| `--interval` | `0.2` | seconds between frames per camera (5 FPS) |
| `--show` | off | a preview window per camera (`q` or Esc quits). Costs some speed; leave off for real runs |
| `--display-width` | `960` | preview width |
| `--crossing-check` | off | moves an open incident across a pure tracker ID swap. Off by default: it cannot tell a swap from a real second violator |
| `--duration` | `0` | stop after N seconds (0 = run until stopped) |
| `--report-every` | `30` | seconds between timing lines in the log and rows in `perf.csv` |
| `--retry` | `10` | seconds between reconnect attempts |

Stop with **Ctrl+C** (or `kill <pid>`, SIGTERM): open incidents are closed (end time = stop time, message 5 sent),
the queue is flushed and the cameras are released, normally in under a second. If the worker is killed hard or
crashes, the next start closes what it left open (end time = start time) before anything else.

**Fake live cameras for testing** (outside the repo, in `~/rads_tools/`: an RTSP server and ffmpeg replaying the two
clips in real time, H.265 2688 × 1520, nothing installed system-wide):

```bash
~/rads_tools/rads_cams.sh start          # rtsp://127.0.0.1:8554/cam1 and /cam2
~/rads_tools/rads_cams.sh stop-cam 1     # "unplug" camera 1;  start-cam 1 plugs it back in
~/rads_tools/rads_cams.sh status | stop
```

## 3. Cameras going away

- RTSP always runs over TCP, with open/read timeouts, so a dead stream never blocks.
- No frame for 5 s → the camera is `reconnecting`; the reader retries every 10 s (`--retry`) and resumes on its own.
  The other camera is not affected.
- On every reconnect (and every loop of a video file) that camera starts afresh: its open incidents are closed
  (end time = last violation), and its tracker, rule state and phone spots are reset.
- Not online for more than 60 s → its open incidents are closed right away (end time = last violation, message 5),
  and heartbeats / camera health report it `offline` until it is back.
- The app server down → the worker carries on and keeps writing rows; messages 2 and 5 are retried 3 times (1 s
  apart) and then given up (the rows are in the DB, the dashboard shows them on refresh); heartbeats are not
  retried.

## 4. Logs and `perf.csv`

- `rads_data/logs/worker.log` (rotating, 5 × 5 MB) and the console: incidents opened/closed, reloads, reconnects,
  and every 30 s a `perf` line per camera (frames processed / skipped / stale, detect ms, lag, tracks) plus the
  loop busy %. At the end a `perf-total` line for the whole run.
- `rads_data/logs/perf.csv`, one row every 30 s (Table 6): time; per camera input fps, processed fps, processed,
  missed (= skipped + stale + errors), skipped, stale, detect ms mean / p95, lag p95; loop busy %; worker RSS
  memory (MB); CPU % (of one core, can be above 100); open incidents; notifier queue sizes.
  Pass rules: missed < 1 %, lag p95 < 200 ms, busy ≤ 70 %, no memory growth.

## 5. The worker's web server (127.0.0.1:8001, this computer only)

| Call | What it does |
|---|---|
| `GET /stream/{camera_id}` | MJPEG live view, ~2 FPS, 1344 px wide, boxes, zones, IDs and states drawn. The newest frame is sent at once on connect. 404 for an unknown camera or one disabled in the DB. The app relays it as `/api/cameras/{id}/stream` |
| `GET /stream/{camera_id}?raw=1` | the same picture without any drawing (for snapshots and the zone editor) |
| `POST /reload` | header `X-RADS-Internal: <token>`; answers `{"ok": true}` at once. Before the next frame the worker re-reads cameras, zones and rules and resets only what changed (one summary line in the log) |
| `GET /health` | per camera status, input / processed fps, last frame time, open incidents and viewers; whether the crossing check is on |

Drawing and JPEG encoding happen only while someone watches; with no viewers the live view costs nothing.

## 6. Recommended Mac settings

- Keep the Mac **on the charger**; on battery macOS slows the GPU and the loop gets late.
- Keep it awake for the whole run: `caffeinate -dimsu` in a terminal (Ctrl+C ends it), or
  `caffeinate -dimsu -w <worker pid>`. Turn off automatic sleep / "Low Power Mode" in System Settings.
- Close heavy apps; the worker needs ~1.2 GB of memory and the GPU.

## 7. Known limits

- **Per-camera judgement.** Each camera is judged on its own; there is no identity across cameras, so a person seen by
  both cameras can produce one incident on each.
- **ID swaps.** When two people cross, the tracker can swap their IDs; this occasionally gives a duplicate incident
  for the same person (the opt-in `--crossing-check` reduces this but can hide a real second violator).
- **H-04 means "a phone is present in the prep area"**, not "a phone is being used": the model sees phones, not how
  they are used.
- **Phone detection is weak** (recall ≈ 0.47), so some phones are missed; H-04 uses a low ratio (0.30) and a long
  window to compensate.
- **Not yet tested on the real cameras.** All tests so far used the two recorded clips, as files and as fake RTSP
  cameras on this Mac.
