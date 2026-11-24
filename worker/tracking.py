"""Person tracking: one ultralytics BYTETracker per camera, fed person detections directly.

We do not use `model.track()`: with two cameras it would mix their IDs. In ultralytics 8.4.216 the tracker has no
frame-rate argument; a lost track is kept for `track_buffer` *processed* frames (`max_frames_lost = track_buffer`),
so `track_buffer = keep_s × fps`. The effective value is read back from the tracker and logged.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from ultralytics.engine.results import Boxes
from ultralytics.trackers.byte_tracker import BYTETracker
from ultralytics.utils import YAML, IterableSimpleNamespace
from ultralytics.utils.checks import check_yaml

log = logging.getLogger(__name__)

PERSON = "person"


@dataclass(frozen=True)
class TrackedPerson:
    track_id: int
    box: tuple[float, float, float, float]  # the detection box (not the Kalman-smoothed one)
    conf: float


class PersonTracker:
    def __init__(self, camera_id: int, fps: float = 5.0, keep_s: float = 5.0, **overrides: Any) -> None:
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self.camera_id = camera_id
        self.fps = fps
        args = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        args.track_buffer = max(1, int(round(keep_s * fps)))
        for k, v in overrides.items():
            setattr(args, k, v)
        if "frame_rate" in inspect.signature(BYTETracker.__init__).parameters:  # older/newer ultralytics
            self._tracker = BYTETracker(args, frame_rate=int(round(fps)))
        else:
            self._tracker = BYTETracker(args)
        self.track_buffer = args.track_buffer
        lost = getattr(self._tracker, "max_frames_lost", None)
        if lost is None:
            lost = getattr(self._tracker, "max_time_lost", args.track_buffer)
        self.max_lost_frames = int(lost)
        self.keep_s_effective = self.max_lost_frames / fps
        self._alive: set[int] = set()
        self.removed_ids: list[int] = []  # track ids the tracker dropped in the last update (lost > keep_s)
        log.info("cam%d tracker: track_buffer=%d -> max_frames_lost=%d at %.1f FPS = %.1f s",
                 camera_id, self.track_buffer, self.max_lost_frames, fps, self.keep_s_effective)

    def update(self, dets: list[dict[str, Any]], frame_shape: tuple[int, int]) -> list[TrackedPerson]:
        """Feed this frame's person detections (call on every processed frame, even with none)."""
        persons = [d for d in dets if d["cls_name"] == PERSON]
        data = np.array([[*d["xyxy"], d["conf"], 0.0] for d in persons], dtype=np.float32).reshape(-1, 6)
        out = self._tracker.update(Boxes(data, tuple(frame_shape[:2])))
        # A track is alive while it is tracked or lost (kept for max_lost_frames); gone = removed.
        alive = {int(t.track_id) for t in (*self._tracker.tracked_stracks, *self._tracker.lost_stracks)
                 if t.is_activated}
        self.removed_ids = sorted(self._alive - alive)
        self._alive = alive
        result = []
        for row in out:
            d = persons[int(row[-1])]
            result.append(TrackedPerson(int(row[4]), tuple(d["xyxy"]), float(d["conf"])))
        return result

    def reset(self) -> None:
        self._tracker.reset()
        self._alive = set()
        self.removed_ids = []
