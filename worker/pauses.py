"""Pause monitoring, worker side: which cameras are paused right now (shared.contract.active_pause).

The watcher asks the DB every CHECK_INTERVAL_S (1 s) and on every /reload, one short read per camera in a single
connection, and caches the answer; the main loop only reads the cache. If the query fails (DB busy, table missing),
the last known state is kept and a warning is logged – the loop never stops because of it.

A pause row may be for one camera or for all (camera_id NULL); active_pause() already resolves that, including
planned pauses (not in force before starts_at) and pauses resumed early (ended_at set). A planned pause therefore
takes effect at the first check after its start, i.e. at most CHECK_INTERVAL_S late; incidents are still closed at
the pause's own start time.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

from shared.contract import TZ, PauseReason, active_pause

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 1.0   # two tiny reads (~1 ms): a pause takes effect within ~1 s
WARN_EVERY_S = 60.0


def _parse(value: str | None) -> datetime | None:
    """Stored ISO text -> aware datetime (no offset = Pakistan time), None if missing or unreadable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt


@dataclass(frozen=True)
class Pause:
    """The pause in force for one camera."""
    pause_id: int
    reason: PauseReason
    starts_at: datetime
    ends_at: datetime | None     # None = until resumed
    all_cameras: bool
    note: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Pause":
        try:
            reason = PauseReason(row["reason"])
        except ValueError:
            reason = PauseReason.OTHER
        return cls(int(row["pause_id"]), reason, _parse(row["starts_at"]) or datetime.now(TZ),
                   _parse(row["ends_at"]), row["camera_id"] is None, row.get("note"))

    @property
    def start_t(self) -> float:
        return self.starts_at.timestamp()

    def until_text(self, now: datetime | None = None) -> str:
        if self.ends_at is None:
            return "until resumed"
        end = self.ends_at.astimezone(TZ)
        today = (now or datetime.now(TZ)).astimezone(TZ).date()
        return f"until {end:%H:%M}" if end.date() == today else f"until {end:%d %b %H:%M}"

    def text(self, dash: str = "–") -> str:
        """DETECTION PAUSED – Cleaning – until 14:30. The live-view banner passes dash='-' (OpenCV draws ASCII)."""
        return f"DETECTION PAUSED {dash} {self.reason.label} {dash} {self.until_text()}"

    def describe(self) -> str:
        return (f"{self.reason.label}, {self.until_text()} (pause {self.pause_id}, "
                f"{'all cameras' if self.all_cameras else 'this camera'})")


class PauseWatcher:
    """Cached answer of active_pause() per camera, refreshed every CHECK_INTERVAL_S or on demand."""

    def __init__(self, engine, camera_ids: Iterable[int], interval_s: float = CHECK_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] | None = None,
                 query: Callable[..., dict | None] = active_pause) -> None:
        self.engine = engine
        self.camera_ids = list(camera_ids)
        self.interval_s = interval_s
        self.clock = clock
        self.now = now or (lambda: datetime.now(TZ))
        self.query = query
        self.state: dict[int, Pause | None] = {c: None for c in self.camera_ids}
        self.errors = 0
        self._next = 0.0          # check at once on the first poll
        self._last_warn = -1e18

    def set_cameras(self, camera_ids: Iterable[int]) -> None:
        self.camera_ids = list(camera_ids)
        self.state = {c: self.state.get(c) for c in self.camera_ids}
        self._next = 0.0

    def request(self) -> None:
        """Check again at the next poll (after /reload)."""
        self._next = 0.0

    def poll(self) -> bool:
        """Refresh if due. Returns True if the DB was read (successfully) this time."""
        t = self.clock()
        if t < self._next:
            return False
        self._next = t + self.interval_s
        return self.refresh()

    def refresh(self) -> bool:
        now = self.now()
        try:
            with self.engine.connect() as con:
                new = {}
                for c in self.camera_ids:
                    row = self.query(con, c, now)
                    new[c] = Pause.from_row(dict(row)) if row else None
        except Exception as e:  # noqa: BLE001 – DB busy, table missing, ...: keep the last known state
            self.errors += 1
            t = self.clock()
            if t - self._last_warn >= WARN_EVERY_S:
                self._last_warn = t
                log.warning("pause check failed (%s: %s) – keeping the last known pause state %s",
                            type(e).__name__, e, {c: (p.pause_id if p else None) for c, p in self.state.items()})
            return False
        self.state = new
        return True
