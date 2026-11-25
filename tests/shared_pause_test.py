"""Pause monitoring contract: the monitoring_pauses table, active_pause(), the new heartbeat fields, and
scripts/migrate_add_pauses.py. Every database here is a temporary copy; the real rads_data/rads.db is never used."""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.migrate_add_pauses import INDEX, TABLE, migrate  # noqa: E402
from shared.config import SCHEMA_FILE  # noqa: E402
from shared.contract import (  # noqa: E402
    PAUSE_REASON_LABELS, TZ, DetectionState, Heartbeat, PauseReason, WsCameraStatus, active_pause,
)
from shared.db import create_schema, make_engine  # noqa: E402

NOW = datetime(2026, 12, 2, 14, 0, 0, tzinfo=TZ)


def at(**delta) -> str:
    """ISO text for NOW + delta, e.g. at(minutes=-10)."""
    return (NOW + timedelta(**delta)).isoformat(timespec="seconds")


@pytest.fixture
def s(tmp_path):
    path = tmp_path / "rads.db"
    create_schema(path)
    engine = make_engine(path)
    Session_ = sessionmaker(bind=engine)
    with Session_() as db:
        db.execute(text("INSERT INTO roles (role_id, name) VALUES (1, 'Admin')"))
        db.execute(text("INSERT INTO users (user_id, role_id, username, password_hash, created_at) "
                        "VALUES (1, 1, 'admin', 'x', :t)"), {"t": at()})
        for cid in (1, 2):
            db.execute(text("INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (:c, :n, 'rtsp://cam')"),
                       {"c": cid, "n": f"Camera {cid}"})
        db.commit()
    session = Session_()
    yield session
    session.close()
    engine.dispose()


def pause(s, camera_id=1, starts=None, ends=None, ended=None, reason="cleaning") -> int:
    pid = s.execute(text(
        "INSERT INTO monitoring_pauses (camera_id, reason, starts_at, ends_at, created_by, created_at, ended_by, "
        "ended_at) VALUES (:c, :r, :s, :e, 1, :t, :eb, :ea) RETURNING pause_id"),
        {"c": camera_id, "r": reason, "s": starts or at(minutes=-10), "e": ends, "t": at(minutes=-15),
         "eb": 1 if ended else None, "ea": ended}).scalar_one()
    s.commit()
    return pid


def pid_of(row):
    return row["pause_id"] if row else None


# ---------------------------------------------------------------- active_pause()

def test_camera_pause_applies_to_that_camera_only(s):
    p = pause(s, camera_id=1, reason="maintenance")
    row = active_pause(s, 1, NOW)
    assert pid_of(row) == p and row["reason"] == "maintenance" and row["camera_id"] == 1
    assert active_pause(s, 2, NOW) is None


def test_all_cameras_pause_applies_to_every_camera(s):
    p = pause(s, camera_id=None, reason="closed")
    assert pid_of(active_pause(s, 1, NOW)) == p and pid_of(active_pause(s, 2, NOW)) == p


def test_future_pause_is_not_active_until_it_starts(s):
    p = pause(s, starts=at(hours=1), ends=at(hours=2))
    assert active_pause(s, 1, NOW) is None
    assert active_pause(s, 1, NOW + timedelta(minutes=59, seconds=59)) is None
    assert pid_of(active_pause(s, 1, NOW + timedelta(hours=1))) == p  # starts_at <= now: active from its start


def test_ended_pause_end_is_exclusive(s):
    p = pause(s, ends=at(minutes=30))
    assert pid_of(active_pause(s, 1, NOW + timedelta(minutes=29, seconds=59))) == p
    assert active_pause(s, 1, NOW + timedelta(minutes=30)) is None  # ends_at > now: over at its end


def test_expired_pause_is_not_active(s):
    pause(s, starts=at(hours=-3), ends=at(hours=-1))
    assert active_pause(s, 1, NOW) is None


def test_manually_ended_pause_is_not_active(s):
    pause(s, ends=None, ended=at(minutes=-1))                  # "until resumed", resumed a minute ago
    pause(s, ends=at(hours=2), ended=at(minutes=-2))           # planned to run on, ended early
    pause(s, starts=at(hours=1), ended=at(minutes=-5))         # planned for later, cancelled
    for now in (NOW, NOW + timedelta(hours=1, minutes=1)):
        assert active_pause(s, 1, now) is None


def test_camera_pause_wins_over_all_cameras_pause(s):
    everywhere = pause(s, camera_id=None, reason="closed", starts=at(minutes=-5))
    own = pause(s, camera_id=1, reason="cleaning", starts=at(minutes=-30))
    assert pid_of(active_pause(s, 1, NOW)) == own
    assert pid_of(active_pause(s, 2, NOW)) == everywhere


def test_now_defaults_to_the_current_time(s):
    real_now = datetime.now(TZ)
    p = pause(s, starts=(real_now - timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert pid_of(active_pause(s, 1)) == p


def test_times_are_compared_as_instants_not_text(s):
    # Stored in UTC: starts 08:55 UTC = 13:55 PKT, ends 09:30 UTC = 14:30 PKT, so it is active at 14:00 PKT.
    # As text the end "...T09:30...+00:00" sorts before now "...T14:00...+05:00", so a text comparison would
    # wrongly call this pause over.
    start = datetime(2026, 12, 2, 8, 55, tzinfo=timezone.utc).isoformat()
    end = datetime(2026, 12, 2, 9, 30, tzinfo=timezone.utc).isoformat()
    p = pause(s, starts=start, ends=end)
    assert pid_of(active_pause(s, 1, NOW)) == p


def test_a_broken_timestamp_is_ignored_and_the_camera_stays_checked(s):
    pause(s, starts="not a time")
    assert active_pause(s, 1, NOW) is None


# ---------------------------------------------------------------- reasons and heartbeat

def test_reasons_match_the_schema_and_have_labels():
    check = re.search(r"reason\s+TEXT NOT NULL CHECK \(reason IN \(([^)]*)\)\)", SCHEMA_FILE.read_text("utf-8"))
    assert [v.strip(" '") for v in check.group(1).split(",")] == [r.value for r in PauseReason]
    assert [r.label for r in PauseReason] == ["Cleaning", "Restaurant closed / off day", "No food preparation",
                                              "Maintenance / technical work", "Other"]
    assert set(PAUSE_REASON_LABELS) == set(PauseReason)


def test_heartbeat_from_an_older_sender_still_validates():
    hb = Heartbeat.model_validate({"camera_id": 1, "status": "online", "fps": 4.9, "ts": at()})
    assert hb.detection == DetectionState.ACTIVE and hb.pause_reason is None
    assert hb.model_dump(mode="json")["detection"] == "active"


def test_heartbeat_with_the_new_fields():
    hb = Heartbeat.model_validate({"camera_id": 2, "status": "online", "fps": 5.0, "ts": at(),
                                   "detection": "paused", "pause_reason": "no_food_prep"})
    assert hb.detection == DetectionState.PAUSED and hb.pause_reason == PauseReason.NO_FOOD_PREP
    ws = WsCameraStatus(**hb.model_dump())  # what the app forwards to dashboards
    assert ws.model_dump(mode="json")["pause_reason"] == "no_food_prep"


@pytest.mark.parametrize("extra", [{"detection": "sleeping"}, {"pause_reason": "lunch"}, {"unknown": 1}])
def test_heartbeat_rejects_bad_values(extra):
    with pytest.raises(ValidationError):
        Heartbeat.model_validate({"camera_id": 1, "status": "online", "ts": at(), **extra})


# ---------------------------------------------------------------- migration

def old_database(path: Path) -> None:
    """A rads.db from before this change: the full schema without the pauses table, with some data in it."""
    create_schema(path)
    con = sqlite3.connect(path)
    con.execute(f"DROP INDEX {INDEX}")
    con.execute(f"DROP TABLE {TABLE}")
    con.execute("INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (1, 'Camera 1', 'rtsp://cam')")
    con.commit()
    con.close()


def columns(path: Path) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(f"PRAGMA table_info({TABLE})").fetchall()
    finally:
        con.close()


def test_migration_adds_the_table_once_and_keeps_the_data(tmp_path):
    db = tmp_path / "old" / "rads.db"
    db.parent.mkdir()
    old_database(db)

    first = migrate(db)
    assert [line.split(":")[0] for line in first] == ["backup written", f"created table {TABLE}",
                                                      f"created index {INDEX}"]
    second = migrate(db)
    assert second == [f"{TABLE} and {INDEX} already exist: nothing to do (no backup needed)"]
    assert len(list(db.parent.glob("rads.db.before-pauses-*"))) == 1

    fresh = tmp_path / "fresh.db"
    create_schema(fresh)
    assert columns(db) == columns(fresh)  # exactly what a new database gets
    con = sqlite3.connect(db)
    assert con.execute("SELECT name FROM cameras").fetchall() == [("Camera 1",)]
    assert con.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (INDEX,)).fetchone()
    con.close()
    backup = sqlite3.connect(next(db.parent.glob("rads.db.before-pauses-*")))
    assert backup.execute("SELECT name FROM cameras").fetchall() == [("Camera 1",)]
    assert backup.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (TABLE,)).fetchone() is None
    backup.close()


def test_migration_adds_only_a_missing_index(tmp_path):
    db = tmp_path / "rads.db"
    create_schema(db)
    con = sqlite3.connect(db)
    con.execute(f"DROP INDEX {INDEX}")
    con.commit()
    con.close()
    assert [line.split(":")[0] for line in migrate(db)] == ["backup written", f"created index {INDEX}"]


def test_migration_command_runs_twice_on_a_scratch_copy(tmp_path):
    data = tmp_path / "rads_data"
    data.mkdir()
    db = data / "rads.db"
    old_database(db)
    env = {**os.environ, "RADS_DB_PATH": str(db), "RADS_DATA_DIR": str(data)}  # both: never the real database
    outputs = []
    for _ in range(2):
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_add_pauses.py")], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        assert f"Database: {db}" in run.stdout
        outputs.append(run.stdout)
    assert f"created table {TABLE}" in outputs[0] and "nothing to do" in outputs[1]
    assert columns(db)
