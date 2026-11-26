#!/usr/bin/env bash
# Stop the RADS demo started by scripts/start_demo.sh: dashboard, then worker, then app server, then the fake
# cameras (if --clips started them) and caffeinate. Uses logs/demo/pids; without that file it finds the processes
# listening on ports 5173, 8001 and 8000 (and stops the fake cameras on 8554).
# Each process gets SIGTERM first (the worker then closes open incidents and flushes its queue), up to 10 s to
# exit, and only then SIGKILL.
set -uo pipefail   # no -e: keep going if one part is already gone

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PIDS="$ROOT/logs/demo/pids"
CAMS_SCRIPT="${RADS_CAMS_SCRIPT:-$HOME/rads_tools/rads_cams.sh}"

alive() { kill -0 "$1" 2>/dev/null; }

# stop_pid(name, pid): SIGTERM to the process group (start_demo.sh gives each part its own) or the process,
# wait up to 10 s, then SIGKILL.
stop_pid() {
  local name="$1" pid="$2" i
  [[ -n "$pid" ]] && alive "$pid" || { echo "  $name: not running"; return 0; }
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for (( i = 0; i < 20; i++ )); do
    alive "$pid" || { echo "  $name: stopped"; return 0; }
    sleep 0.5
  done
  echo "  $name: did not stop within 10 s, forcing"
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
  sleep 0.5
}

# listener(port): pid of the process listening on that port (empty if none)
listener() { lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n 1; }

stop_cams() {
  if [[ -x "$CAMS_SCRIPT" ]]; then
    "$CAMS_SCRIPT" stop > /dev/null 2>&1 && echo "  fake cameras: stopped"
  else
    stop_pid "fake cameras (port 8554)" "$(listener 8554)"
  fi
}

echo "Stopping RADS ..."
PORTS="8000 8001 5173"   # 8554 is checked only when this script is responsible for the fake cameras
if [[ -f "$PIDS" ]]; then
  pid_of() { awk -v n="$1" '$1 == n { print $2 }' "$PIDS"; }
  stop_pid dashboard "$(pid_of dashboard)"
  stop_pid worker "$(pid_of worker)"
  stop_pid "app server" "$(pid_of app)"
  if grep -q '^cams ' "$PIDS"; then stop_cams; PORTS="$PORTS 8554"; fi
  stop_pid caffeinate "$(pid_of caffeinate)"
  rm -f "$PIDS"
else
  echo "  (no logs/demo/pids: looking for the processes on the RADS ports)"
  stop_pid "dashboard (port 5173)" "$(listener 5173)"
  stop_pid "worker (port 8001)" "$(listener 8001)"
  stop_pid "app server (port 8000)" "$(listener 8000)"
  if [[ -n "$(listener 8554)" ]]; then stop_cams; fi
  PORTS="$PORTS 8554"
fi

# anything still listening on a RADS port (e.g. started by hand) is reported, not killed silently
still=0
for port in $PORTS; do
  holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 { print $1 " (PID " $2 ")" }')"
  if [[ -n "$holder" ]]; then
    echo "  port $port is still in use by $holder"
    still=1
  fi
done
if (( still )); then
  echo "Some ports are still in use (see above). Run scripts/stop_demo.sh again, or stop those processes by hand."
  exit 1
fi
echo "  ports ${PORTS// /, } are free"
echo "RADS stopped"
