"""Timing statistics for the scheduler: per-camera detect / total / lag times, skipped slots and loop busy %.

Pure logic (no OpenCV, no model) so it can be unit-tested. All times are `time.perf_counter()` seconds; the
recorded durations are milliseconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


def advance_slot(slot_t: float, now: float, period: float) -> tuple[float, int]:
    """Skip rule for one camera's processing slots.

    A slot is the window [slot_t, slot_t + period). If `now` is already past the end of the window, the whole
    periods that have passed are skipped (counted as missed) and the start of the current window is returned.
    Returns (slot start to use, number of skipped slots).
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    if now < slot_t + period:
        return slot_t, 0
    k = int((now - slot_t) // period)
    return slot_t + k * period, k


def _stats(values: list[float], pcts: tuple[int, ...]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, **{f"p{p}": None for p in pcts}, "max": None}
    a = np.asarray(values, dtype=float)
    out: dict[str, float | None] = {"mean": round(float(a.mean()), 1)}
    for p in pcts:
        out[f"p{p}"] = round(float(np.percentile(a, p)), 1)
    out["max"] = round(float(a.max()), 1)
    return out


@dataclass
class CamStats:
    detect_ms: list[float] = field(default_factory=list)
    total_ms: list[float] = field(default_factory=list)
    lag_ms: list[float] = field(default_factory=list)
    skipped: int = 0
    stale: int = 0
    errors: int = 0

    @property
    def processed(self) -> int:
        return len(self.total_ms)


class PerfWindow:
    """Accumulates timings for a set of cameras over one reporting window (or the whole run)."""

    def __init__(self, camera_ids: list[int], start_t: float) -> None:
        self.camera_ids = list(camera_ids)
        self.reset(start_t)

    def reset(self, start_t: float) -> None:
        self.start_t = start_t
        self.cams: dict[int, CamStats] = {c: CamStats() for c in self.camera_ids}
        self.gui_s = 0.0  # preview-window event pumping, done in idle time; not part of busy

    def add_gui(self, seconds: float) -> None:
        self.gui_s += seconds

    def record(self, cam: int, detect_ms: float, total_ms: float, lag_ms: float) -> None:
        s = self.cams[cam]
        s.detect_ms.append(detect_ms)
        s.total_ms.append(total_ms)
        s.lag_ms.append(lag_ms)

    def skip(self, cam: int, n: int = 1) -> None:
        self.cams[cam].skipped += n

    def stale(self, cam: int, n: int = 1) -> None:
        """No new frame since the last processed one (or no frame at all)."""
        self.cams[cam].stale += n

    def error(self, cam: int, n: int = 1) -> None:
        self.cams[cam].errors += n

    def busy_s(self) -> float:
        return sum(sum(s.total_ms) for s in self.cams.values()) / 1e3

    def summary(self, now: float) -> dict[str, Any]:
        wall = max(now - self.start_t, 1e-9)
        cams = {}
        for c, s in self.cams.items():
            cams[c] = {
                "processed": s.processed, "skipped": s.skipped, "stale": s.stale, "errors": s.errors,
                "detect_ms": _stats(s.detect_ms, (95,)),
                "total_ms": _stats(s.total_ms, (95,)),
                "lag_ms": _stats(s.lag_ms, (50, 95)),
            }
        return {"wall_s": round(wall, 1), "busy_pct": round(100.0 * self.busy_s() / wall, 1),
                "gui_pct": round(100.0 * self.gui_s / wall, 1), "cams": cams}
