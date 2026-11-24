"""Rule engine: sliding windows, confirm / close, cooldown. One RuleEngine per camera; pure logic, no I/O.

Each processed frame adds at most one observation per (hazard, key):
- worker hazards (H-01, H-06, H-08; tied_to = worker): key = the worker (track id, see "ID switches"), only while
  the worker's feet are in one of the rule's zones. Linked violation part -> violation, linked compliant part ->
  compliant, neither (or below the rule's conf_threshold) -> unseen (not counted).
- zone hazards with a compliant class (H-05 bin): key = zone. Bin centre in the zone: bin_open -> violation,
  bin_closed -> compliant; no bin seen -> unseen.
- zone hazards without a compliant class (H-04 phone): key = (zone, phone spot). A phone at that spot -> violation,
  no phone there -> compliant. Spots: each phone joins the nearest spot whose last centre is within 150 px, otherwise
  it starts a new one; a spot is forgotten after end_gap_s + cooldown_s without detections.
- If both classes are present for one key in one frame, the higher confidence wins.

Confirm (only on a violation frame, so the evidence shows it): seen >= ceil(window_s x fps / 2) observations in the
last window_s, violation / seen >= min_ratio, no open incident for the key, cooldown for the key has passed.
start = first violation inside that window.
Close: (a) no violation for end_gap_s AND at least one compliant observation since the last violation,
(b) the worker's feet left the rule's zones, (c) the tracker dropped the track. Unseen frames never close.
end_time = time of the last violation (stop: the stop time). Cooldown (per hazard and key) starts at the close.

ID switches (worker hazards): a new track takes over a worker who is no longer visible – same key, same window,
same open incident (row and track_id) and cooldowns – when either guard matches:
- position guard: that worker had an open or recent incident, was last seen within cooldown_s, and the new box
  overlaps the last box (IoU >= 0.3) or its centre is within one box width of it;
- head-count guard: that worker was last seen within the tracker's keep time (5 s) and the number of tracked people
  did not go up (one out, one in) – e.g. two workers overlapping at a counter and the tracker swapping IDs.
Several lost/new tracks are paired nearest last box first (position matches before head-count matches). Two workers
visible at the same time are never merged. Every takeover is logged.
Crossing check (pure ID swaps, worker hazards): two tracks that stay tracked may simply trade IDs when they overlap,
so no new track is born and neither guard applies. If worker B is about to confirm hazard h, and exactly one worker A
that overlapped B (IoU > 0) within the last 10 s has an OPEN incident for h but no violation of h since the earliest
of those overlaps, and B showed no violation of h in the 10 s before that earliest overlap (B's violations only
began after the crossing), A's open incident moves to B instead of a new one opening (same incident row, its
track_id kept; logged as "swap transfer"). B continues it and A starts fresh. More than one such A: nothing special.
Clip loop (video files): restart() closes the camera's open incidents and forgets all its state.
"""
from __future__ import annotations

import itertools
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

from shared.contract import TiedTo
from worker.linking import CLASS_OF, PARTS, Worker
from worker.settings import Hazard, Rule
from worker.zones import centre

log = logging.getLogger(__name__)

VIOLATION = "violation"
COMPLIANT = "compliant"
SPOT_RADIUS_PX = 150.0
SWITCH_MIN_IOU = 0.3
CROSSING_WINDOW_S = 10.0  # crossing check: how far back an overlap of two tracks counts

_REFS: Iterator[int] = itertools.count(1)  # incident refs, unique in this process (all cameras)

Box = Sequence[float]
Key = tuple  # ("track", id) | ("zone", zone_id) | ("spot", zone_id, spot_id)


# --------------------------------------------------------------------------- events (to the notifier)


@dataclass(frozen=True)
class IncidentOpen:
    ref: int                    # engine-local id; the notifier maps it to incidents.incident_id
    camera_id: int
    rule: Rule
    hazard_id: str
    zone_id: int
    track_id: int | None        # worker hazards only
    spot_id: int | None         # H-04 only (not stored in the DB)
    start_t: float              # epoch seconds
    t: float                    # confirm time = time of the evidence frame
    conf_avg: float
    conf_max: float
    trigger: dict[str, Any]     # the violating detection {cls_name, conf, xyxy}
    subject_box: tuple | None   # the worker's box (worker hazards)


@dataclass(frozen=True)
class IncidentClose:
    ref: int
    camera_id: int
    hazard_id: str
    end_t: float
    conf_avg: float
    conf_max: float
    reason: str


Event = IncidentOpen | IncidentClose


# --------------------------------------------------------------------------- helpers


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _dist(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def min_seen(rule: Rule, fps: float) -> int:
    """Observations needed in the window: half of the frames expected (5 s x 5 FPS -> 13 of 25)."""
    return math.ceil(round(rule.window_s * fps / 2.0, 6))


@dataclass
class Spot:
    spot_id: int
    zone_id: int
    centre: tuple[float, float]
    last_seen_t: float


def match_spots(spots: list[Spot], dets: list[dict[str, Any]], radius: float = SPOT_RADIUS_PX
                ) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Assign phone detections to spots: nearest pairs within `radius` first, one detection per spot; a further
    detection within `radius` of an already matched spot is a duplicate box of the same phone (the more confident
    one is kept). Returns ({spot_id: detection}, detections that start new spots)."""
    pairs = sorted(((_dist(centre(d["xyxy"]), s.centre), i, s.spot_id)
                    for i, d in enumerate(dets) for s in spots), key=lambda p: p[0])
    matched: dict[int, dict[str, Any]] = {}
    used: set[int] = set()
    for dist, i, sid in pairs:
        if dist > radius:
            break
        if i in used or sid in matched:
            continue
        matched[sid] = dets[i]
        used.add(i)
    new = []
    by_id = {s.spot_id: s for s in spots}
    for i, d in enumerate(dets):
        if i in used:
            continue
        c = centre(d["xyxy"])
        dup = next((sid for sid in matched if _dist(c, by_id[sid].centre) <= radius), None)
        if dup is not None:
            if d["conf"] > matched[dup]["conf"]:
                matched[dup] = d
            continue
        # also merge with another new detection close by (two boxes on one new phone)
        twin = next((k for k, n in enumerate(new) if _dist(c, centre(n["xyxy"])) <= radius), None)
        if twin is not None:
            if d["conf"] > new[twin]["conf"]:
                new[twin] = d
            continue
        new.append(d)
    return matched, new


# --------------------------------------------------------------------------- state


@dataclass
class _Incident:
    ref: int
    start_t: float
    confs: list[float]
    last_violation_t: float
    last_compliant_t: float = -math.inf


@dataclass
class _KeyState:
    rule: Rule
    obs: deque = field(default_factory=deque)  # (t, kind, conf) of seen frames inside the window
    incident: _Incident | None = None
    cooldown_until: float = -math.inf
    violation_times: deque = field(default_factory=deque)  # violations of the last 2 x CROSSING_WINDOW_S (crossing check)


@dataclass
class _WorkerIdentity:
    """One worker across tracker ID switches; the key of worker hazards is ("track", canonical)."""
    canonical: int
    live: set[int]               # tracker ids currently mapped to this worker (tracked or lost, not removed)
    last_box: tuple
    last_seen_t: float
    visible_count: int = 1       # tracked people visible in the frame where this worker was last seen


class RuleEngine:
    def __init__(self, camera_id: int, rules: Iterable[Rule], hazards: dict[str, Hazard], fps: float,
                 track_keep_s: float = 5.0) -> None:
        self.camera_id = camera_id
        self.fps = fps
        self.track_keep_s = track_keep_s  # how long the tracker keeps a lost track (head-count guard window)
        self.takeovers = 0
        self.swap_transfers = 0
        self._overlaps: dict[tuple[int, int], deque] = {}  # (worker, worker) -> times their boxes overlapped
        self.rules = [r for r in rules if r.enabled and r.camera_id == camera_id and r.hazard_id in hazards]
        self.hazards = hazards
        self._states: dict[tuple[str, Key], _KeyState] = {}
        self._alias: dict[int, int] = {}                   # tracker id -> canonical worker id
        self._workers: dict[int, _WorkerIdentity] = {}
        self._spots: dict[tuple[str, int], list[Spot]] = {}  # (hazard, zone) -> spots
        self._next_spot = itertools.count(1)
        worker_rules = [r for r in self.rules if hazards[r.hazard_id].tied_to == TiedTo.WORKER]
        self._max_worker_cooldown = max((r.cooldown_s for r in worker_rules), default=0.0)

    # ------------------------------------------------------------------ public

    def update(self, t: float, workers: list[Worker], objects: list[tuple[dict[str, Any], tuple[int, ...]]],
               lost_track_ids: Iterable[int] = ()) -> list[Event]:
        """One processed frame at epoch time `t`. `objects` = (detection, zone ids) of phones/bins kept by the
        ignore policy; `lost_track_ids` = tracks the tracker dropped this frame."""
        events: list[Event] = []
        self._resolve_identities(t, workers)
        for rule in self.rules:
            hz = self.hazards[rule.hazard_id]
            if hz.tied_to == TiedTo.WORKER:
                self._worker_rule(t, rule, hz, workers, events)
            elif hz.compliant_class:
                self._zone_rule(t, rule, hz, objects, events)
            else:
                self._spot_rule(t, rule, hz, objects, events)
        self._handle_lost(t, lost_track_ids, events)
        self._check_gaps(t, events)
        self._purge(t)
        return events

    def close_all(self, t: float, reason: str = "worker stopped") -> list[IncidentClose]:
        """Close every open incident now (end_time = t), e.g. on a clean stop."""
        events: list[Event] = []
        for (hz, _key), st in self._states.items():
            if st.incident is not None:
                self._close(st, hz, t, t, reason, events)
        return events  # type: ignore[return-value]

    def restart(self, reason: str = "clip looped") -> list[IncidentClose]:
        """Camera restart (a video file looping back to its start): close every open incident with end_time =
        its last violation, then forget all state – windows, cooldowns, workers, phone spots."""
        events: list[Event] = []
        for (hz, _key), st in self._states.items():
            if st.incident is not None:
                self._close(st, hz, st.incident.last_violation_t, st.incident.last_violation_t, reason, events)
        self._states.clear()
        self._alias.clear()
        self._workers.clear()
        self._spots.clear()
        self._overlaps.clear()
        return events  # type: ignore[return-value]

    def open_incidents(self) -> list[tuple[str, Key]]:
        return [k for k, st in self._states.items() if st.incident is not None]

    def spots(self, hazard_id: str, zone_id: int) -> list[Spot]:
        return list(self._spots.get((hazard_id, zone_id), []))

    # ------------------------------------------------------------------ observations and confirm

    def _observe(self, t: float, rule: Rule, hz: Hazard, key: Key, kind: str | None, conf: float, *,
                 zone_id: int, track_id: int | None = None, spot_id: int | None = None,
                 trigger: dict[str, Any] | None = None, subject_box: tuple | None = None,
                 events: list[Event]) -> None:
        st = self._states.get((hz.hazard_id, key))
        if st is None:
            st = self._states[(hz.hazard_id, key)] = _KeyState(rule)
        st.rule = rule
        if kind is None:  # unseen: not counted, never closes
            return
        st.obs.append((t, kind, conf))
        while st.obs and st.obs[0][0] <= t - rule.window_s:
            st.obs.popleft()
        if kind == VIOLATION and key[0] == "track":
            st.violation_times.append(t)
            while st.violation_times and st.violation_times[0] < t - 2 * CROSSING_WINDOW_S:
                st.violation_times.popleft()
        inc = st.incident
        if inc is not None:
            if kind == VIOLATION:
                inc.confs.append(conf)
                inc.last_violation_t = t
            else:
                inc.last_compliant_t = t
            return
        if kind != VIOLATION or t < st.cooldown_until:
            return
        seen = len(st.obs)
        viol = [o for o in st.obs if o[1] == VIOLATION]
        if seen < min_seen(rule, self.fps) or len(viol) / seen < rule.min_ratio:
            return
        if key[0] == "track" and self._swap_transfer(t, hz.hazard_id, key, st, conf):
            return
        confs = [c for _, _, c in viol]
        st.incident = _Incident(next(_REFS), viol[0][0], confs, last_violation_t=t)
        events.append(IncidentOpen(st.incident.ref, self.camera_id, rule, hz.hazard_id, zone_id, track_id, spot_id,
                                   viol[0][0], t, sum(confs) / len(confs), max(confs), trigger or {}, subject_box))
        log.info("cam%d %s opened ref=%d key=%s ratio=%d/%d", self.camera_id, hz.hazard_id, st.incident.ref,
                 key, len(viol), seen)

    def _close(self, st: _KeyState, hazard_id: str, t: float, end_t: float, reason: str,
               events: list[Event]) -> None:
        inc = st.incident
        assert inc is not None
        events.append(IncidentClose(inc.ref, self.camera_id, hazard_id, end_t, sum(inc.confs) / len(inc.confs),
                                    max(inc.confs), reason))
        log.info("cam%d %s closed ref=%d (%s)", self.camera_id, hazard_id, inc.ref, reason)
        st.incident = None
        st.cooldown_until = t + st.rule.cooldown_s
        st.obs.clear()

    def _check_gaps(self, t: float, events: list[Event]) -> None:
        """Close (a): no violation for end_gap_s and something compliant seen since the last violation."""
        for (hz, _key), st in self._states.items():
            inc = st.incident
            if (inc is not None and t - inc.last_violation_t >= st.rule.end_gap_s
                    and inc.last_compliant_t > inc.last_violation_t):
                self._close(st, hz, t, inc.last_violation_t, "compliant", events)

    # ------------------------------------------------------------------ worker hazards

    def _worker_rule(self, t: float, rule: Rule, hz: Hazard, workers: list[Worker], events: list[Event]) -> None:
        kind_name = PARTS[hz.violation_class][0]  # head | mask | apron
        for w in workers:
            canon = self._alias.get(w.track_id, w.track_id)
            key: Key = ("track", canon)
            zones_in = [z for z in rule.zone_ids if z in w.zone_ids]
            if not zones_in:
                st = self._states.get((hz.hazard_id, key))
                if st is not None and st.incident is not None:  # close (b): feet left the rule's zones
                    self._close(st, hz.hazard_id, t, st.incident.last_violation_t, "left zone", events)
                continue
            state, conf = getattr(w, kind_name), w.confs.get(kind_name, 0.0)
            kind = None
            if state is not None and conf >= rule.conf_threshold:
                cls = CLASS_OF[(kind_name, state)]
                kind = VIOLATION if cls == hz.violation_class else COMPLIANT if cls == hz.compliant_class else None
            self._observe(t, rule, hz, key, kind, conf, zone_id=zones_in[0], track_id=canon,
                          trigger=w.parts.get(kind_name), subject_box=tuple(w.box), events=events)

    def _resolve_identities(self, t: float, workers: list[Worker]) -> None:
        visible = {w.track_id: w for w in workers}
        new = [tid for tid in visible if tid not in self._alias]
        if new:
            self._take_over(t, new, visible)
        # two tracks of one worker visible at the same time -> they are two workers after all
        by_canon: dict[int, list[int]] = {}
        for tid in visible:
            by_canon.setdefault(self._alias[tid], []).append(tid)
        for canon, tids in by_canon.items():
            if len(tids) > 1:
                keep = canon if canon in tids else min(tids)
                for tid in tids:
                    if tid != keep:
                        self._alias[tid] = tid
                        self._workers[canon].live.discard(tid)
                        self._workers[tid] = _WorkerIdentity(tid, {tid}, tuple(visible[tid].box), t)
        for tid, w in visible.items():
            wi = self._workers[self._alias[tid]]
            wi.last_box, wi.last_seen_t, wi.visible_count = tuple(w.box), t, len(visible)
        self._record_overlaps(t, workers)

    def _record_overlaps(self, t: float, workers: list[Worker]) -> None:
        for i, a in enumerate(workers):
            for b in workers[i + 1:]:
                if iou(a.box, b.box) > 0:
                    ca, cb = self._alias[a.track_id], self._alias[b.track_id]
                    if ca != cb:
                        self._overlaps.setdefault((min(ca, cb), max(ca, cb)), deque()).append(t)
        for pair in list(self._overlaps):
            times = self._overlaps[pair]
            while times and t - times[0] > CROSSING_WINDOW_S:
                times.popleft()
            if not times:
                del self._overlaps[pair]

    def _swap_transfer(self, t: float, hazard_id: str, key: Key, st: _KeyState, conf: float) -> bool:
        """Crossing check (see the module docstring). True if A's open incident was moved to `key` (= B)."""
        b = key[1]
        candidates = []
        for (x, y), times in self._overlaps.items():
            if b not in (x, y):
                continue
            a = y if x == b else x
            st_a = self._states.get((hazard_id, ("track", a)))
            if st_a is None or st_a.incident is None:
                continue
            first_overlap = times[0]
            if st_a.incident.last_violation_t >= first_overlap:
                continue  # A violated since the overlap: A is still the violator
            if any(first_overlap - CROSSING_WINDOW_S <= v < first_overlap for v in st.violation_times):
                continue  # B was already violating before the crossing: B is a violator of his own
            candidates.append((a, st_a, first_overlap))
        if len(candidates) != 1:
            return False
        a, st_a, first_overlap = candidates[0]
        inc = st_a.incident
        st_a.incident = None          # A starts fresh: no incident, no cooldown, empty window
        st_a.obs.clear()
        st_a.violation_times.clear()
        inc.confs.append(conf)
        inc.last_violation_t = t
        inc.last_compliant_t = -math.inf
        st.incident = inc
        self.swap_transfers += 1
        log.info("cam%d %s: swap transfer – open incident ref=%d moves from worker %d to worker %d (overlapped "
                 "%.1f s ago, worker %d no violation since)", self.camera_id, hazard_id, inc.ref, a, b,
                 t - first_overlap, a)
        return True

    def _has_memory(self, canon: int, t: float) -> bool:
        """An open incident, or a cooldown still running, for this worker."""
        for (hz, key), st in self._states.items():
            if key == ("track", canon) and (st.incident is not None or st.cooldown_until > t):
                return True
        return False

    def _take_over(self, t: float, new: list[int], visible: dict[int, Worker]) -> None:
        """Pair new tracks with workers who are no longer visible (position guard, then head-count guard,
        nearest last box first); unpaired new tracks become new workers."""
        gone = {c: wi for c, wi in self._workers.items() if not any(tid in visible for tid in wi.live)}
        pairs = []
        for tid in new:
            box = visible[tid].box
            for canon, wi in gone.items():
                ago = t - wi.last_seen_t
                ov = iou(box, wi.last_box)
                d = _dist(centre(box), centre(wi.last_box))
                position = (ago <= self._max_worker_cooldown and self._has_memory(canon, t)
                            and (ov >= SWITCH_MIN_IOU or d <= wi.last_box[2] - wi.last_box[0]))
                head_count = ago <= self.track_keep_s and len(visible) <= wi.visible_count
                if position or head_count:
                    pairs.append((0 if position else 1, d, tid, canon, ov, ago))
        pairs.sort(key=lambda p: (p[0], p[1]))
        paired_new: set[int] = set()
        paired_old: set[int] = set()
        for kind, d, tid, canon, ov, ago in pairs:
            if tid in paired_new or canon in paired_old:
                continue
            paired_new.add(tid)
            paired_old.add(canon)
            self._alias[tid] = canon
            self._workers[canon].live.add(tid)
            self.takeovers += 1
            why = (f"position guard: IoU {ov:.2f}, {d:.0f} px" if kind == 0 else
                   f"head-count guard: {len(visible)} visible, was {self._workers[canon].visible_count}, {d:.0f} px")
            log.info("cam%d: track %d takes over worker %d, lost %.1f s ago (%s)", self.camera_id, tid, canon, ago, why)
        for tid in new:
            if tid not in paired_new:
                self._alias[tid] = tid
                self._workers[tid] = _WorkerIdentity(tid, {tid}, tuple(visible[tid].box), t, len(visible))

    def _handle_lost(self, t: float, lost_track_ids: Iterable[int], events: list[Event]) -> None:
        for tid in lost_track_ids:
            canon = self._alias.pop(tid, None)
            if canon is None:
                continue
            wi = self._workers.get(canon)
            if wi is None:
                continue
            wi.live.discard(tid)
            if wi.live:
                continue  # another track still carries this worker
            for (hz, key), st in self._states.items():
                if key == ("track", canon) and st.incident is not None:  # close (c): the tracker dropped it
                    self._close(st, hz, t, st.incident.last_violation_t, "track lost", events)

    # ------------------------------------------------------------------ zone hazards

    def _zone_rule(self, t: float, rule: Rule, hz: Hazard, objects, events: list[Event]) -> None:
        for z in rule.zone_ids:
            cands = [o for o, zids in objects if z in zids and o["conf"] >= rule.conf_threshold
                     and o["cls_name"] in (hz.violation_class, hz.compliant_class)]
            if not cands:
                self._observe(t, rule, hz, ("zone", z), None, 0.0, zone_id=z, events=events)  # unseen
                continue
            best = max(cands, key=lambda o: o["conf"])
            kind = VIOLATION if best["cls_name"] == hz.violation_class else COMPLIANT
            self._observe(t, rule, hz, ("zone", z), kind, best["conf"], zone_id=z, trigger=best, events=events)

    def _spot_rule(self, t: float, rule: Rule, hz: Hazard, objects, events: list[Event]) -> None:
        for z in rule.zone_ids:
            phones = [o for o, zids in objects if z in zids and o["cls_name"] == hz.violation_class
                      and o["conf"] >= rule.conf_threshold]
            spots = self._spots.setdefault((hz.hazard_id, z), [])
            matched, new = match_spots(spots, phones)
            for d in new:
                s = Spot(next(self._next_spot), z, centre(d["xyxy"]), t)
                spots.append(s)
                matched[s.spot_id] = d
                log.info("cam%d %s: new phone spot %d at (%.0f, %.0f) in zone %d", self.camera_id, hz.hazard_id,
                         s.spot_id, *s.centre, z)
            for s in spots:
                d = matched.get(s.spot_id)
                if d is not None:
                    s.centre, s.last_seen_t = centre(d["xyxy"]), t
                    self._observe(t, rule, hz, ("spot", z, s.spot_id), VIOLATION, d["conf"], zone_id=z,
                                  spot_id=s.spot_id, trigger=d, events=events)
                else:  # no phone at this spot -> compliant
                    self._observe(t, rule, hz, ("spot", z, s.spot_id), COMPLIANT, 0.0, zone_id=z,
                                  spot_id=s.spot_id, events=events)
            # forget spots without detections for end_gap_s + cooldown_s (never one with an open incident)
            keep = []
            for s in spots:
                st = self._states.get((hz.hazard_id, ("spot", z, s.spot_id)))
                if t - s.last_seen_t > rule.end_gap_s + rule.cooldown_s and (st is None or st.incident is None):
                    self._states.pop((hz.hazard_id, ("spot", z, s.spot_id)), None)
                    log.info("cam%d %s: phone spot %d forgotten", self.camera_id, hz.hazard_id, s.spot_id)
                else:
                    keep.append(s)
            spots[:] = keep

    # ------------------------------------------------------------------ housekeeping

    def _purge(self, t: float) -> None:
        """Drop workers that are gone, with no open incident and no running cooldown."""
        for canon in [c for c, wi in self._workers.items() if not wi.live]:
            if not self._has_memory(canon, t):
                del self._workers[canon]
                for k in [k for k in self._states if k[1] == ("track", canon)]:
                    del self._states[k]
                for pair in [pr for pr in self._overlaps if canon in pr]:
                    del self._overlaps[pair]
