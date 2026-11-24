"""Attach head / mask / apron boxes to tracked persons.

- head_* and mask_*: part centre inside the person box AND in its top third.
- apron / no_apron: part centre inside the person box AND in its middle third.
- If several persons qualify, the smallest person box wins. Unlinked parts are dropped.
- If a person gets several parts of one kind, the most confident one wins.
- Persons ignored by an ignore zone (feet inside it) take part in the competition too: a part that goes to an
  ignored person is dropped with them (a customer's head never lands on a worker behind the counter).
  Parts are never filtered by ignore zones on their own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from worker.tracking import TrackedPerson
from worker.zones import box_area, centre

# class name -> (part kind, state). Names come from model.names; ids are never used.
PARTS: dict[str, tuple[str, str]] = {
    "head_covered": ("head", "covered"),
    "head_uncovered": ("head", "uncovered"),
    "mask_on": ("mask", "on"),
    "mask_off": ("mask", "off"),
    "apron": ("apron", "yes"),
    "no_apron": ("apron", "no"),
}
CLASS_OF: dict[tuple[str, str], str] = {v: k for k, v in PARTS.items()}  # (kind, state) -> class name
BAND = {"head": 0, "mask": 0, "apron": 1}  # which third of the person box (0 = top, 1 = middle)
VIOLATION_STATES = {"head": "uncovered", "mask": "off", "apron": "no"}


@dataclass
class Worker:
    track_id: int
    box: tuple[float, float, float, float]
    head: str | None = None    # covered | uncovered | None
    mask: str | None = None    # on | off | None
    apron: str | None = None   # yes | no | None
    confs: dict[str, float] = field(default_factory=dict)  # person, head, mask, apron
    zone_ids: tuple[int, ...] = ()
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)  # kind -> the linked detection (for evidence)

    def violations(self) -> list[str]:
        return [k for k, bad in VIOLATION_STATES.items() if getattr(self, k) == bad]


def _in_band(box, point, band: int) -> bool:
    x1, y1, x2, y2 = box
    px, py = point
    if not (x1 <= px <= x2 and y1 <= py <= y2):
        return False
    h = y2 - y1
    lo, hi = y1 + band * h / 3.0, y1 + (band + 1) * h / 3.0
    return lo <= py <= hi


def link_parts(persons: Iterable[TrackedPerson], dets: Iterable[dict[str, Any]],
               ignored: Sequence[Sequence[float]] = (), dropped: list[dict[str, Any]] | None = None) -> list[Worker]:
    """Link parts to tracked `persons`. `ignored` = boxes of persons removed by an ignore zone; parts that go to
    one of them are appended to `dropped` (if given) instead of being linked."""
    persons = list(persons)
    workers = [Worker(p.track_id, p.box, confs={"person": p.conf}) for p in persons]
    for d in dets:
        part = PARTS.get(d["cls_name"])
        if part is None:
            continue
        kind, state = part
        c = centre(d["xyxy"])
        # (area, kept-first tie-break, id, worker or None for an ignored person)
        candidates = [(box_area(w.box), 0, w.track_id, w) for w in workers if _in_band(w.box, c, BAND[kind])]
        candidates += [(box_area(b), 1, i, None) for i, b in enumerate(ignored) if _in_band(b, c, BAND[kind])]
        if not candidates:
            continue  # unlinked part: dropped
        w = min(candidates, key=lambda t: t[:3])[3]
        if w is None:  # belongs to an ignored person
            if dropped is not None:
                dropped.append(d)
            continue
        if d["conf"] > w.confs.get(kind, -1.0):
            setattr(w, kind, state)
            w.confs[kind] = float(d["conf"])
            w.parts[kind] = d
    return workers
