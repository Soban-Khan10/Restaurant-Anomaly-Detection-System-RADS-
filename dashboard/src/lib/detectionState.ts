// Is detection paused on a camera? Two sources can say so:
//   - the database (GET /api/monitoring/status, reloaded on every monitoring.changed event): what was decided;
//   - the camera system's heartbeat (camera.status, every 5 s: detection "active" | "paused"): what it is doing.
// Right after a pause is created, ended or cancelled they can disagree for a few seconds, until the worker has
// reloaded. The dashboard then shows the database state with a small "applying…" note.
import type { CameraMonitoring, CameraStatusMessage } from "./types";

export interface DetectionView {
  /** Shown as paused: the database state when it is known, otherwise the heartbeat. */
  paused: boolean;
  /** The camera system has not caught up with the database yet ("applying…"). */
  applying: boolean;
}

export const APPLYING_NOTE = "applying…";
export const APPLYING_HINT = "The camera system has not picked up this change yet. It usually does within a few seconds.";

export function detectionView(db: Pick<CameraMonitoring, "state"> | undefined,
  beat: Pick<CameraStatusMessage, "status" | "detection"> | undefined): DetectionView {
  const beatPaused = beat?.detection === "paused";
  if (!db) return { paused: beatPaused, applying: false }; // status not loaded yet: the heartbeat is all we know
  const paused = db.state === "paused";
  // Only a live heartbeat that reports its detection can disagree. An offline camera carries the last known value
  // and its camera system is not running, so there is nothing being applied.
  const applying = !!beat && beat.status !== "offline" && beat.detection !== undefined && beatPaused !== paused;
  return { paused, applying };
}
