"""Worker settings from the database: cameras, zones, hazards, rules (+ rule_zones) as plain frozen dataclasses.

Read only – the worker never writes these tables. Call `load_settings()` again to reload (Phase 4 /reload).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import Engine, MetaData, select

from shared.contract import TiedTo, ZoneType

log = logging.getLogger(__name__)

Point = tuple[float, float]


@dataclass(frozen=True)
class Camera:
    camera_id: int
    name: str
    rtsp_url: str
    enabled: bool
    target_fps: float
    frame_width: int | None
    frame_height: int | None


@dataclass(frozen=True)
class Zone:
    zone_id: int
    camera_id: int
    name: str
    type: ZoneType
    polygon: tuple[Point, ...]
    active: bool


@dataclass(frozen=True)
class Hazard:
    hazard_id: str
    name: str
    violation_class: str
    compliant_class: str | None
    tied_to: TiedTo


@dataclass(frozen=True)
class Rule:
    rule_id: int
    hazard_id: str
    camera_id: int
    enabled: bool
    conf_threshold: float
    window_s: float
    min_ratio: float
    end_gap_s: float
    cooldown_s: float
    severity: str
    zone_ids: tuple[int, ...]


@dataclass(frozen=True)
class WorkerSettings:
    cameras: dict[int, Camera] = field(default_factory=dict)
    zones: dict[int, Zone] = field(default_factory=dict)
    hazards: dict[str, Hazard] = field(default_factory=dict)
    rules: dict[int, Rule] = field(default_factory=dict)

    def zones_for(self, camera_id: int) -> list[Zone]:
        """Active zones of one camera."""
        return [z for z in self.zones.values() if z.camera_id == camera_id and z.active]

    def rules_for(self, camera_id: int) -> list[Rule]:
        """Enabled rules of one camera."""
        return [r for r in self.rules.values() if r.camera_id == camera_id and r.enabled]


def parse_polygon(text: str) -> tuple[Point, ...]:
    """'[[x, y], ...]' -> ((x, y), ...); raises ValueError if it is not a list of number pairs."""
    pts = json.loads(text)
    if not isinstance(pts, list):
        raise ValueError("polygon is not a list")
    out = []
    for p in pts:
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise ValueError(f"bad point {p!r}")
        out.append((float(p[0]), float(p[1])))
    return tuple(out)


def load_settings(engine: Engine | None = None) -> WorkerSettings:
    if engine is None:
        from shared.db import get_engine
        engine = get_engine()
    md = MetaData()
    md.reflect(bind=engine, only=["cameras", "zones", "hazards", "rules", "rule_zones"])
    t = md.tables

    with engine.connect() as con:
        cameras = {
            r.camera_id: Camera(r.camera_id, r.name, r.rtsp_url, bool(r.enabled), float(r.target_fps),
                                r.frame_width, r.frame_height)
            for r in con.execute(select(t["cameras"]))
        }
        zones = {}
        for r in con.execute(select(t["zones"])):
            try:
                poly = parse_polygon(r.polygon)
            except (ValueError, TypeError) as e:
                log.warning("zone %d (%s): bad polygon JSON, skipped: %s", r.zone_id, r.name, e)
                continue
            zones[r.zone_id] = Zone(r.zone_id, r.camera_id, r.name, ZoneType(r.type), poly, bool(r.active))
        hazards = {
            r.hazard_id: Hazard(r.hazard_id, r.name, r.violation_class, r.compliant_class, TiedTo(r.tied_to))
            for r in con.execute(select(t["hazards"]))
        }
        rz: dict[int, list[int]] = {}
        for r in con.execute(select(t["rule_zones"])):
            rz.setdefault(r.rule_id, []).append(r.zone_id)
        rules = {
            r.rule_id: Rule(r.rule_id, r.hazard_id, r.camera_id, bool(r.enabled), float(r.conf_threshold),
                            float(r.window_s), float(r.min_ratio), float(r.end_gap_s), float(r.cooldown_s),
                            r.severity, tuple(sorted(rz.get(r.rule_id, ()))))
            for r in con.execute(select(t["rules"]))
        }
    s = WorkerSettings(cameras, zones, hazards, rules)
    log.info("settings loaded: %d cameras, %d zones, %d hazards, %d rules",
             len(cameras), len(zones), len(hazards), len(rules))
    return s
