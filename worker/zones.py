"""Zones: point-in-polygon with shapely, and the ignore-zone policy.

Zone membership: a worker is in a zone if the bottom-centre of the person box (the feet) is in the polygon;
objects (phone, bins) use the box centre. Points ON the boundary count as inside, for every zone type (a box
cut off by the bottom of the frame has its feet exactly on the edge of a full-frame polygon).

Ignore zones (`apply_ignore`):
- persons: ignored only if their feet are in an ignore zone; their linked parts are dropped with them (linking).
- body parts (head_*, mask_*, apron, no_apron): never judged on their own; they follow the person they link to.
- held objects (phone): decided by the smallest person box containing the phone's centre – a kept person keeps it,
  an ignored person removes it; a loose phone (no person box around it) is removed if its centre is in an ignore zone.
- everything else (bins, other classes): removed if the box centre is in an ignore zone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import shapely
from shapely.geometry import Polygon
from shapely.validation import make_valid

from shared.contract import ZoneType
from worker.settings import Zone

log = logging.getLogger(__name__)

Box = Sequence[float]  # x1, y1, x2, y2
ACTIVE_TYPES = (ZoneType.PREP, ZoneType.WASTE)
PERSON = "person"
BODY_PART_CLASSES = frozenset({"head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron"})
HELD_CLASSES = frozenset({"phone"})  # objects a worker can hold; they follow the person holding them


def bottom_centre(box: Box) -> tuple[float, float]:
    x1, _, x2, y2 = box[:4]
    return ((x1 + x2) / 2.0, float(y2))


def centre(box: Box) -> tuple[float, float]:
    x1, y1, x2, y2 = box[:4]
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def anchor(det: dict[str, Any]) -> tuple[float, float]:
    """Feet for a person, box centre for everything else."""
    return bottom_centre(det["xyxy"]) if det["cls_name"] == PERSON else centre(det["xyxy"])


def box_contains(box: Box, point: tuple[float, float]) -> bool:
    x1, y1, x2, y2 = box[:4]
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def box_area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


@dataclass(frozen=True)
class FrameCheck:
    zone_id: int
    name: str
    outside: bool        # a vertex lies outside the frame
    coverage_pct: float  # share of the frame area covered by the zone


@dataclass
class IgnoreResult:
    persons: list[dict[str, Any]] = field(default_factory=list)          # kept person detections
    ignored_persons: list[dict[str, Any]] = field(default_factory=list)  # feet in an ignore zone
    parts: list[dict[str, Any]] = field(default_factory=list)            # all body parts (decided by linking)
    objects: list[dict[str, Any]] = field(default_factory=list)          # kept phones, bins, other classes
    removed: list[dict[str, Any]] = field(default_factory=list)          # objects removed by the ignore policy


class CameraZones:
    def __init__(self, zones: Iterable[Zone]) -> None:
        self.zones: list[Zone] = []
        self._geom: dict[int, Any] = {}
        for z in zones:
            if not z.active:
                continue
            if len(z.polygon) < 3:
                log.warning("zone %d (%s): needs at least 3 points, skipped", z.zone_id, z.name)
                continue
            g = Polygon(z.polygon)
            if not g.is_valid:
                log.warning("zone %d (%s): polygon is self-intersecting, repaired", z.zone_id, z.name)
                g = make_valid(g)
            if g.is_empty or g.area <= 0:
                log.warning("zone %d (%s): polygon has no area, skipped", z.zone_id, z.name)
                continue
            shapely.prepare(g)
            self.zones.append(z)
            self._geom[z.zone_id] = g

    def of_type(self, *types: ZoneType) -> list[Zone]:
        return [z for z in self.zones if z.type in types]

    def contains(self, zone_id: int, point: tuple[float, float]) -> bool:
        """Point in polygon, boundary included (for a point, `intersects` == `covers`; `contains` would exclude
        the edge)."""
        return bool(shapely.intersects_xy(self._geom[zone_id], *point))

    def zones_at(self, point: tuple[float, float], types: Iterable[ZoneType] = ACTIVE_TYPES) -> tuple[int, ...]:
        types = tuple(types)
        return tuple(z.zone_id for z in self.of_type(*types) if self.contains(z.zone_id, point))

    def in_ignore(self, point: tuple[float, float]) -> bool:
        return bool(self.zones_at(point, (ZoneType.IGNORE,)))

    def person_zones(self, box: Box) -> tuple[int, ...]:
        return self.zones_at(bottom_centre(box))

    def object_zones(self, box: Box) -> tuple[int, ...]:
        return self.zones_at(centre(box))

    def apply_ignore(self, dets: list[dict[str, Any]]) -> IgnoreResult:
        """Split this frame's detections by the ignore-zone policy (see the module docstring)."""
        r = IgnoreResult()
        has_ignore = bool(self.of_type(ZoneType.IGNORE))
        for d in dets:
            if d["cls_name"] == PERSON:
                (r.ignored_persons if has_ignore and self.in_ignore(bottom_centre(d["xyxy"])) else r.persons).append(d)
        for d in dets:
            name = d["cls_name"]
            if name == PERSON:
                continue
            if name in BODY_PART_CLASSES:
                r.parts.append(d)
                continue
            if not has_ignore:
                r.objects.append(d)
                continue
            c = centre(d["xyxy"])
            if name in HELD_CLASSES:
                holders = [(box_area(p["xyxy"]), i, keep) for keep, group in ((True, r.persons), (False, r.ignored_persons))
                           for i, p in enumerate(group) if box_contains(p["xyxy"], c)]
                if holders:
                    keep = min(holders, key=lambda h: (h[0], h[1]))[2]  # the smallest person box around it
                    (r.objects if keep else r.removed).append(d)
                    continue
            (r.removed if self.in_ignore(c) else r.objects).append(d)
        return r

    def check_frame(self, width: int, height: int) -> list[FrameCheck]:
        frame = Polygon([(0, 0), (width, 0), (width, height), (0, height)])
        out = []
        for z in self.zones:
            outside = any(not (0 <= x <= width and 0 <= y <= height) for x, y in z.polygon)
            cov = 100.0 * self._geom[z.zone_id].intersection(frame).area / (width * height)
            out.append(FrameCheck(z.zone_id, z.name, outside, round(cov, 1)))
        return out
