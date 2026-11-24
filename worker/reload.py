"""Settings reload (POST /reload): validate the freshly read settings and work out what changed.

Pure functions; the scheduler applies the result (worker/main.py, Scheduler._reload).
- validate(): a bad item in the DB (unparsable or degenerate polygon, impossible rule value) never replaces a good
  one – the previous version is kept and an ERROR is logged; a new bad item is left out.
- diff(): which rules changed (and how), which must close their incidents (disabled, deleted, zones changed), which
  zones changed (polygon, type, active) and which cameras changed (rtsp_url, enabled).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from worker.settings import Rule, WorkerSettings
from worker.zones import polygon_ok

log = logging.getLogger(__name__)

RULE_VALUE_FIELDS = ("enabled", "conf_threshold", "window_s", "min_ratio", "end_gap_s", "cooldown_s", "severity",
                     "zone_ids")


def rule_problem(r: Rule) -> str | None:
    if not 0.0 <= r.conf_threshold <= 1.0:
        return f"conf_threshold {r.conf_threshold} not in [0, 1]"
    if r.window_s <= 0:
        return f"window_s {r.window_s} must be > 0"
    if not 0.0 < r.min_ratio <= 1.0:
        return f"min_ratio {r.min_ratio} not in (0, 1]"
    if r.end_gap_s < 0 or r.cooldown_s < 0:
        return f"end_gap_s {r.end_gap_s} / cooldown_s {r.cooldown_s} must be >= 0"
    return None


def validate(old: WorkerSettings, new: WorkerSettings) -> tuple[WorkerSettings, list[str]]:
    """Keep the previous version of every invalid item. Returns (settings to use, error messages)."""
    errors: list[str] = []
    zones = dict(new.zones)
    for zid in new.bad_zone_ids:
        if zid in old.zones:
            zones[zid] = old.zones[zid]
            errors.append(f"zone {zid}: polygon in the DB is not valid JSON – keeping the previous polygon")
        else:
            errors.append(f"zone {zid}: polygon in the DB is not valid JSON – zone left out")
    for zid, z in new.zones.items():
        if not polygon_ok(z.polygon):
            if zid in old.zones:
                zones[zid] = old.zones[zid]
                errors.append(f"zone {zid} '{z.name}': polygon {list(z.polygon)} is not a usable polygon – "
                              f"keeping the previous polygon")
            else:
                del zones[zid]
                errors.append(f"zone {zid} '{z.name}': polygon {list(z.polygon)} is not usable – zone left out")
    rules = dict(new.rules)
    for rid, r in new.rules.items():
        problem = rule_problem(r)
        if problem:
            if rid in old.rules:
                rules[rid] = old.rules[rid]
                errors.append(f"rule {rid} ({r.hazard_id} cam{r.camera_id}): {problem} – keeping the previous values")
            else:
                del rules[rid]
                errors.append(f"rule {rid} ({r.hazard_id} cam{r.camera_id}): {problem} – rule left out")
    for e in errors:
        log.error("reload: %s", e)
    return replace(new, zones=zones, rules=rules, bad_zone_ids=()), errors


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(list(v) if isinstance(v, tuple) else v)


@dataclass
class SettingsDiff:
    changed_rules: dict[int, list[str]] = field(default_factory=dict)  # values changed, zones unchanged
    closed_rules: dict[int, str] = field(default_factory=dict)         # disabled / deleted / zones changed
    added_rules: set[int] = field(default_factory=set)
    changed_zones: dict[int, str] = field(default_factory=dict)
    changed_cameras: dict[int, str] = field(default_factory=dict)

    def empty(self) -> bool:
        return not (self.changed_rules or self.closed_rules or self.added_rules or self.changed_zones
                    or self.changed_cameras)


def diff(old: WorkerSettings, new: WorkerSettings) -> SettingsDiff:
    d = SettingsDiff()
    for rid in sorted(set(old.rules) | set(new.rules)):
        o, n = old.rules.get(rid), new.rules.get(rid)
        o_on, n_on = o is not None and o.enabled, n is not None and n.enabled
        name = f"{(n or o).hazard_id} cam{(n or o).camera_id}"
        if o_on and not n_on:
            d.closed_rules[rid] = f"{name}: {'deleted' if n is None else 'disabled'}"
        elif n_on and not o_on:
            d.added_rules.add(rid)
        elif o_on and n_on and o != n:
            if (o.hazard_id, o.camera_id) != (n.hazard_id, n.camera_id):
                d.closed_rules[rid] = f"{name}: hazard/camera changed"
                d.added_rules.add(rid)
            elif o.zone_ids != n.zone_ids:
                d.closed_rules[rid] = f"{name}: zones {list(o.zone_ids)}→{list(n.zone_ids)}"
            else:
                d.changed_rules[rid] = [f"{name}: {f} {_fmt(getattr(o, f))}→{_fmt(getattr(n, f))}"
                                        for f in RULE_VALUE_FIELDS if getattr(o, f) != getattr(n, f)]
    for zid in sorted(set(old.zones) | set(new.zones)):
        o, n = old.zones.get(zid), new.zones.get(zid)
        if o is None or n is None:
            d.changed_zones[zid] = f"zone {zid}: {'added' if o is None else 'deleted'}"
        else:
            what = [f for f in ("polygon", "type", "active") if getattr(o, f) != getattr(n, f)]
            if what:
                d.changed_zones[zid] = f"zone {zid} '{n.name}' cam{n.camera_id}: {', '.join(what)}"
    for cid in sorted(set(old.cameras) | set(new.cameras)):
        o, n = old.cameras.get(cid), new.cameras.get(cid)
        if o is None or n is None:
            d.changed_cameras[cid] = f"camera {cid}: {'added' if o is None else 'deleted'}"
        else:
            what = [f for f in ("rtsp_url", "enabled") if getattr(o, f) != getattr(n, f)]
            if what:
                d.changed_cameras[cid] = f"camera {cid}: {', '.join(what)}"
    return d


def zone_camera(zone_id: int, *settings: WorkerSettings) -> int | None:
    for s in settings:
        if zone_id in s.zones:
            return s.zones[zone_id].camera_id
    return None
