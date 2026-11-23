"""Seed rads.db from config/defaults.yaml + .env (shared contract section 4).

Inserts roles, one admin user, the 5 hazards, cameras (RTSP from CAM1_RTSP / CAM2_RTSP), zones and rules.
Safe to re-run: rows that already exist are left untouched (dashboard edits are never overwritten).

    python scripts/seed.py                      # asks for the admin password
    python scripts/seed.py --username boss
    echo secret | python scripts/seed.py --password-stdin   # non-interactive (tests / CI)
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/seed.py`

import bcrypt  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from shared.config import get_settings, load_defaults  # noqa: E402
from shared.contract import RoleName, ZoneType, now_iso  # noqa: E402
from shared.db import session_scope  # noqa: E402

MIN_PASSWORD_LEN = 8


def ask_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("Admin password: ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match.")
    if len(pw) < MIN_PASSWORD_LEN:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if len(pw.encode("utf-8")) > 72:
        sys.exit("Password must be at most 72 bytes (bcrypt limit).")
    return pw


def seed_roles(s: Session, roles: list[str]) -> None:
    for name in roles:
        RoleName(name)  # validate against the contract
        s.execute(text("INSERT OR IGNORE INTO roles (name) VALUES (:n)"), {"n": name})
    print(f"roles:   {', '.join(roles)}")


def seed_admin(s: Session, cfg: dict, username: str, from_stdin: bool) -> None:
    exists = s.execute(text("SELECT 1 FROM users WHERE username = :u"), {"u": username}).first()
    if exists:
        print(f"admin:   '{username}' already exists, skipped")
        return
    pw = ask_password(from_stdin)
    role_id = s.execute(text("SELECT role_id FROM roles WHERE name = 'Admin'")).scalar_one()
    s.execute(
        text("INSERT INTO users (role_id, username, password_hash, full_name, active, created_at) "
             "VALUES (:r, :u, :h, :f, 1, :t)"),
        {"r": role_id, "u": username,
         "h": bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii"),
         "f": cfg.get("full_name"), "t": now_iso()},
    )
    print(f"admin:   '{username}' created")


def seed_hazards(s: Session, hazards: list[dict]) -> None:
    for h in hazards:
        s.execute(
            text("INSERT OR IGNORE INTO hazards (hazard_id, name, violation_class, compliant_class, tied_to) "
                 "VALUES (:hazard_id, :name, :violation_class, :compliant_class, :tied_to)"), h)
    print(f"hazards: {', '.join(h['hazard_id'] for h in hazards)}")


def seed_cameras(s: Session, cameras: list[dict]) -> None:
    settings = get_settings()
    for c in cameras:
        rtsp = settings.camera_rtsp(c["rtsp_env"])
        if not rtsp:
            sys.exit(f"{c['rtsp_env']} is not set. Copy .env.example to .env and fill it in.")
        res = s.execute(
            text("INSERT OR IGNORE INTO cameras (camera_id, name, rtsp_url, enabled, target_fps) "
                 "VALUES (:id, :name, :url, 1, :fps)"),
            {"id": c["camera_id"], "name": c["name"], "url": rtsp, "fps": c.get("target_fps", 5)})
        state = "created" if res.rowcount else "exists, skipped"
        print(f"camera:  {c['camera_id']} '{c['name']}' {state}")


def seed_zones(s: Session, zones: list[dict]) -> None:
    for z in zones:
        ZoneType(z["type"])
        exists = s.execute(text("SELECT 1 FROM zones WHERE camera_id = :c AND name = :n"),
                           {"c": z["camera_id"], "n": z["name"]}).first()
        if exists:
            print(f"zone:    cam{z['camera_id']} '{z['name']}' exists, skipped")
            continue
        s.execute(
            text("INSERT INTO zones (camera_id, name, type, polygon, active) VALUES (:c, :n, :t, :p, 1)"),
            {"c": z["camera_id"], "n": z["name"], "t": z["type"], "p": json.dumps(z["polygon"])})
        print(f"zone:    cam{z['camera_id']} '{z['name']}' ({z['type']}) created")


def seed_rules(s: Session, rules_cfg: dict, hazards: list[dict], cameras: list[dict]) -> None:
    defaults = rules_cfg["defaults"]
    overrides = rules_cfg.get("overrides") or {}
    zone_type = rules_cfg["zone_type"]
    for cam in cameras:
        cam_id = cam["camera_id"]
        for h in hazards:
            hz = h["hazard_id"]
            zone_ids = [r[0] for r in s.execute(
                text("SELECT zone_id FROM zones WHERE camera_id = :c AND type = :t ORDER BY zone_id"),
                {"c": cam_id, "t": zone_type[hz]})]
            if not zone_ids:
                print(f"rule:    {hz} cam{cam_id} skipped (no {zone_type[hz]} zone on this camera)")
                continue
            existing = s.execute(text("SELECT rule_id FROM rules WHERE hazard_id = :h AND camera_id = :c"),
                                 {"h": hz, "c": cam_id}).scalar()
            if existing is not None:
                print(f"rule:    {hz} cam{cam_id} exists, skipped")
                continue
            v = {**defaults, **overrides.get(hz, {})}
            rule_id = s.execute(
                text("INSERT INTO rules (hazard_id, camera_id, enabled, conf_threshold, window_s, min_ratio, "
                     "end_gap_s, cooldown_s, severity) "
                     "VALUES (:h, :c, :en, :ct, :w, :mr, :eg, :cd, :sev) RETURNING rule_id"),
                {"h": hz, "c": cam_id, "en": int(bool(v["enabled"])), "ct": v["conf_threshold"],
                 "w": v["window_s"], "mr": v["min_ratio"], "eg": v["end_gap_s"], "cd": v["cooldown_s"],
                 "sev": v["severity"]}).scalar_one()
            for zid in zone_ids:
                s.execute(text("INSERT INTO rule_zones (rule_id, zone_id) VALUES (:r, :z)"),
                          {"r": rule_id, "z": zid})
            print(f"rule:    {hz} cam{cam_id} created -> zones {zone_ids} "
                  f"(conf {v['conf_threshold']}, ratio {v['min_ratio']})")


def main() -> int:
    cfg = load_defaults()
    parser = argparse.ArgumentParser(description="Seed the RADS database from config/defaults.yaml.")
    parser.add_argument("--username", default=cfg["admin"]["username"], help="admin username")
    parser.add_argument("--password-stdin", action="store_true",
                        help="read the admin password from stdin instead of prompting")
    args = parser.parse_args()

    db_path = get_settings().db_path
    if not db_path.exists():
        print(f"No database at {db_path}. Run: python scripts/init_db.py")
        return 1

    with session_scope() as s:
        seed_roles(s, cfg["roles"])
        seed_admin(s, cfg["admin"], args.username, args.password_stdin)
        seed_hazards(s, cfg["hazards"])
        seed_cameras(s, cfg["cameras"])
        seed_zones(s, cfg["zones"])
        seed_rules(s, cfg["rules"], cfg["hazards"], cfg["cameras"])
    print("Seed complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
