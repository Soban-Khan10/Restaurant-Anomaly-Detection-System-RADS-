#!/usr/bin/env bash
# Start the whole RADS system for a demo: app server, pipeline worker and dashboard, in that order.
#
#   scripts/start_demo.sh            the real cameras (each enabled camera's address from the database)
#   scripts/start_demo.sh --clips    the two recorded clips as fake live cameras (rtsp://127.0.0.1:8554/cam1, /cam2),
#                                    using the fake-camera script from worker/README_worker.md (~/rads_tools/rads_cams.sh)
#
# Stop everything with scripts/stop_demo.sh.
# Output goes to logs/demo/{app,worker,dashboard}.log; process ids to logs/demo/pids.
# Settings come from .env as usual; variables already set in the environment win (e.g. RADS_DATA_DIR for a test run).
# RADS_DEMO_NO_BROWSER=1 skips opening the browser. RADS_CAMS_SCRIPT overrides the fake-camera script's location.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
LOGS="$ROOT/logs/demo"
PIDS="$LOGS/pids"
CAMS_SCRIPT="${RADS_CAMS_SCRIPT:-$HOME/rads_tools/rads_cams.sh}"

APP_URL="http://127.0.0.1:8000"
WORKER_URL="http://127.0.0.1:8001"
DASH_URL="http://localhost:5173"

fail() { echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------- options
CLIPS=0
for arg in "$@"; do
  case "$arg" in
    --clips) CLIPS=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) fail "unknown option '$arg' (only --clips is supported)" ;;
  esac
done

# ---------------------------------------------------------------- 1. prerequisites
[[ -x .venv/bin/python ]] || fail ".venv is missing. Create it first (README.md, step 2)."
[[ -f .env ]] || fail ".env is missing. Copy .env.example to .env and fill it in (README.md, step 3)."
[[ -d dashboard/node_modules ]] || fail "dashboard/node_modules is missing. Run: cd dashboard && npm install"
# shellcheck disable=SC1091
source .venv/bin/activate
# the model file the worker loads (RADS_MODEL_PATH, default ./models/v2_960_best.pt)
MODEL="$(python -c 'from shared.config import get_settings; print(get_settings().model_path)')"
[[ -f "$MODEL" ]] || fail "the model file is missing: $MODEL (set RADS_MODEL_PATH in .env or copy the model there)."

# ---------------------------------------------------------------- 2. ports must be free
port_holder() {  # "<command> (PID <pid>)" of the process listening on port $1, empty if the port is free
  { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; } | awk 'NR == 2 { print $1 " (PID " $2 ")" }'
}
busy=0
for port in 8000 8001 5173; do
  holder="$(port_holder "$port")"
  if [[ -n "$holder" ]]; then
    echo "Port $port is already in use by $holder." >&2
    busy=1
  fi
done
if (( busy )); then
  fail "RADS (or something else) is already running. Stop it first with scripts/stop_demo.sh."
fi

mkdir -p "$LOGS"
: > "$PIDS"

# start(name, log, command...): run in the background in its own process group (so stop_demo.sh can stop the
# command and everything it starts, e.g. npm -> vite), and record "<name> <pid>" in logs/demo/pids.
start() {
  local name="$1" log="$2"; shift 2
  echo "===== $(date '+%F %T') start" >> "$log"
  perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV or die "exec: $!"' -- "$@" >> "$log" 2>&1 < /dev/null &
  echo "$name $!" >> "$PIDS"
}

# wait_for(name, url, seconds, log): wait until url answers; otherwise show the log, stop what runs, exit
wait_for() {
  local name="$1" url="$2" secs="$3" log="$4" i
  for (( i = 0; i < secs * 2; i++ )); do
    if curl -s -o /dev/null --max-time 2 "$url"; then
      echo "  $name: OK"
      return 0
    fi
    sleep 0.5
  done
  echo "ERROR: $name did not answer at $url within $secs s. Last 20 lines of $log:" >&2
  tail -n 20 "$log" >&2 || true
  echo "Stopping what was already started ..." >&2
  "$ROOT/scripts/stop_demo.sh" >&2 || true
  exit 1
}

# ---------------------------------------------------------------- 8. optional: fake cameras from the recorded clips
WORKER_ARGS=()
if (( CLIPS )); then
  if [[ -x "$CAMS_SCRIPT" ]]; then
    echo "Starting the fake cameras (recorded clips) ..."
    # its own output names the stream addresses: keep it in the log, not on screen
    if "$CAMS_SCRIPT" start >> "$LOGS/cameras.log" 2>&1; then
      echo "cams $CAMS_SCRIPT" >> "$PIDS"
      echo "  fake cameras: OK"
      WORKER_ARGS=(--source1 rtsp://127.0.0.1:8554/cam1 --source2 rtsp://127.0.0.1:8554/cam2)
    else
      echo "ERROR: the fake cameras did not start. Last 20 lines of $LOGS/cameras.log:" >&2
      tail -n 20 "$LOGS/cameras.log" >&2 || true
      "$ROOT/scripts/stop_demo.sh" >&2 || true
      exit 1
    fi
  else
    echo "Note: --clips ignored: no fake-camera script found at $CAMS_SCRIPT (see worker/README_worker.md)."
    echo "      The worker will use the real cameras."
  fi
fi

# ---------------------------------------------------------------- 3. start app, worker, dashboard
echo "Starting RADS ..."
start app "$LOGS/app.log" python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
wait_for "app server (127.0.0.1:8000)" "$APP_URL/docs" 30 "$LOGS/app.log"

start worker "$LOGS/worker.log" python -m worker.main "${WORKER_ARGS[@]+"${WORKER_ARGS[@]}"}"
wait_for "pipeline worker (127.0.0.1:8001)" "$WORKER_URL/health" 60 "$LOGS/worker.log"

start dashboard "$LOGS/dashboard.log" npm --prefix "$ROOT/dashboard" run dev
wait_for "dashboard (localhost:5173)" "$DASH_URL" 30 "$LOGS/dashboard.log"

# ---------------------------------------------------------------- 5. keep the Mac awake while the worker runs
WORKER_PID="$(awk '$1 == "worker" { print $2 }' "$PIDS")"
caffeinate -dimsu -w "$WORKER_PID" > /dev/null 2>&1 &
echo "caffeinate $!" >> "$PIDS"
disown -a

# ---------------------------------------------------------------- 6. status
echo
echo "RADS is running:"
echo "  app server  OK  $APP_URL   (API docs: $APP_URL/docs)"
echo "  worker      OK  $WORKER_URL"
echo "  dashboard   OK  $DASH_URL"
echo "  keep awake  OK  (caffeinate, ends with the worker)"
echo "Cameras:"
# names from the database, status from the worker's /health – never the camera addresses
curl -s --max-time 5 "$WORKER_URL/health" | python -c '
import json, sqlite3, sys
from shared.config import get_settings
health = json.load(sys.stdin).get("cameras", {})
con = sqlite3.connect(get_settings().db_path)
names = dict(con.execute("SELECT camera_id, name FROM cameras"))
con.close()
for cid, h in sorted(health.items(), key=lambda kv: int(kv[0])):
    status = "online" if h.get("status") == "online" else "offline"
    name = names.get(int(cid)) or ("Camera %s" % cid)
    print("  %s: %s" % (name, status))
if not health:
    print("  (no cameras enabled)")
' || echo "  (camera status not available)"
echo "Logs: logs/demo/   Stop: scripts/stop_demo.sh"
echo
echo "Open $DASH_URL"

# ---------------------------------------------------------------- 7. open the dashboard
if [[ "${RADS_DEMO_NO_BROWSER:-0}" != "1" ]]; then
  open "$DASH_URL" || true
fi
