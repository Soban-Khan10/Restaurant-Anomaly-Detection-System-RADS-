"""E-mail alerts contract: users.email, 'email' in alerts.channel, and scripts/migrate_add_email.py.
Every database here is a temporary copy; the real rads_data/rads.db is never used."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.migrate_add_email import MigrationError, migrate  # noqa: E402
from shared.config import SCHEMA_FILE  # noqa: E402
from shared.contract import AlertChannel  # noqa: E402

NEW_CHECK = "CHECK (channel IN ('dashboard','whatsapp','telegram','email'))"
OLD_CHECK = "CHECK (channel IN ('dashboard','whatsapp','telegram'))"
T = "2026-12-01T10:00:00+05:00"


def old_schema_sql() -> str:
    """shared/schema.sql as it was before this change: no users.email, no 'email' channel."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    assert "  email         TEXT,\n" in sql and NEW_CHECK in sql
    return sql.replace("  email         TEXT,\n", "").replace(NEW_CHECK, OLD_CHECK)


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def old_database(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(old_schema_sql())
    con.executescript(f"""
        INSERT INTO roles (role_id, name) VALUES (1, 'Admin'), (2, 'Manager');
        INSERT INTO users (user_id, role_id, username, password_hash, phone, created_at)
            VALUES (1, 1, 'admin', 'x', NULL, '{T}'), (2, 2, 'ahmad', 'y', '+923001234567', '{T}');
        INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (1, 'Camera 1', 'rtsp://cam');
        INSERT INTO hazards (hazard_id, name, violation_class, compliant_class, tied_to)
            VALUES ('H-01', 'Head cover missing', 'head_uncovered', 'head_covered', 'worker');
        INSERT INTO zones (zone_id, camera_id, name, type, polygon) VALUES (1, 1, 'Prep', 'prep', '[[0,0],[9,0],[9,9]]');
        INSERT INTO rules (rule_id, hazard_id, camera_id) VALUES (1, 'H-01', 1);
        INSERT INTO incidents (incident_id, hazard_id, rule_id, camera_id, zone_id, start_time, severity, model_version)
            VALUES (1, 'H-01', 1, 1, 1, '{T}', 'Medium', 'test');
        INSERT INTO alerts (alert_id, incident_id, user_id, channel, kind, sent_at, status, error) VALUES
            (1, 1, NULL, 'dashboard', 'first', '{T}', 'sent', NULL),
            (2, 1, 2, 'whatsapp', 'first', '{T}', 'failed', 'HTTP 400: outside 24 h window'),
            (3, 1, NULL, 'telegram', 'escalation', '{T}', 'sent', NULL);
        CREATE INDEX ix_alerts_incident ON alerts(incident_id);
    """)
    con.commit()
    con.close()


def rows(path: Path, table: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        con.close()


def add_alert(path: Path, channel: str, incident_id: int = 1) -> None:
    con = connect(path)
    try:
        con.execute("INSERT INTO alerts (incident_id, channel, kind, sent_at, status) VALUES (?, ?, 'first', ?, 'sent')",
                    (incident_id, channel, T))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def old_db(tmp_path):
    db = tmp_path / "data" / "rads.db"
    db.parent.mkdir()
    old_database(db)
    return db


# ---------------------------------------------------------------- the contract

def test_contract_lists_email():
    assert AlertChannel.EMAIL.value == "email"
    assert [c.value for c in AlertChannel] == ["dashboard", "whatsapp", "telegram", "email"]


def test_fresh_init_db_has_the_column_and_the_channel(tmp_path):
    data = tmp_path / "rads_data"
    env = {**os.environ, "RADS_DB_PATH": str(data / "rads.db"), "RADS_DATA_DIR": str(data)}  # never the real DB
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "init_db.py")], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    db = data / "rads.db"
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(users)")]
    assert cols.index("email") == cols.index("phone") + 1
    con.close()
    con = connect(db)  # a fresh DB has no rows: add an incident for the alerts to point at
    con.executescript(f"""
        INSERT INTO cameras (camera_id, name, rtsp_url) VALUES (1, 'Camera 1', 'rtsp://cam');
        INSERT INTO hazards (hazard_id, name, violation_class, compliant_class, tied_to)
            VALUES ('H-01', 'x', 'a', 'b', 'worker');
        INSERT INTO zones (zone_id, camera_id, name, type, polygon) VALUES (1, 1, 'Prep', 'prep', '[]');
        INSERT INTO rules (rule_id, hazard_id, camera_id) VALUES (1, 'H-01', 1);
        INSERT INTO incidents (incident_id, hazard_id, rule_id, camera_id, zone_id, start_time, severity, model_version)
            VALUES (1, 'H-01', 1, 1, 1, '{T}', 'Low', 't');""")
    con.close()
    add_alert(db, "email")
    with pytest.raises(sqlite3.IntegrityError):
        add_alert(db, "sms")


# ---------------------------------------------------------------- the migration

def test_old_database_rejects_email_before_the_migration(old_db):
    with pytest.raises(sqlite3.IntegrityError):
        add_alert(old_db, "email")


def test_migration_keeps_every_row_and_allows_email(old_db):
    alerts_before, users_before = rows(old_db, "alerts"), rows(old_db, "users")
    done = migrate(old_db)
    assert [line.split(":")[0] for line in done] == [
        "backup written", "added column users.email",
        "rebuilt alerts"]  # "rebuilt alerts: channel now allows 'email' (...)" split at the first colon
    assert rows(old_db, "alerts") == alerts_before                        # every row, every value
    assert rows(old_db, "users") == [u + (None,) for u in users_before]  # email added (NULL) at the end
    add_alert(old_db, "email")
    with pytest.raises(sqlite3.IntegrityError):
        add_alert(old_db, "sms")                                          # the CHECK still works
    with pytest.raises(sqlite3.IntegrityError):
        add_alert(old_db, "dashboard", incident_id=999)                   # and the foreign keys too
    con = sqlite3.connect(old_db)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_alerts_incident'").fetchone()
    assert NEW_CHECK in con.execute("SELECT sql FROM sqlite_master WHERE name = 'alerts'").fetchone()[0]
    assert con.execute("SELECT name FROM sqlite_master WHERE name = 'alerts_new'").fetchone() is None
    assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    con.close()


def test_migration_twice_changes_nothing_and_backs_up_once(old_db):
    migrate(old_db)
    after_first = rows(old_db, "alerts"), rows(old_db, "users")
    assert migrate(old_db) == ["users.email exists and alerts already allows 'email': nothing to do (no backup needed)"]
    assert (rows(old_db, "alerts"), rows(old_db, "users")) == after_first
    backups = list(old_db.parent.glob("rads.db.before-email-*"))
    assert len(backups) == 1
    assert rows(backups[0], "alerts") == after_first[0]  # the backup holds the data as it was


def test_column_already_there_only_rebuilds_alerts(old_db):
    con = sqlite3.connect(old_db)
    con.execute("ALTER TABLE users ADD COLUMN email TEXT")
    con.commit()
    con.close()
    assert [line.split(":")[0] for line in migrate(old_db)] == ["backup written", "rebuilt alerts"]


def test_a_failed_check_rolls_everything_back(old_db):
    con = sqlite3.connect(old_db)  # foreign keys off: plant an alert whose incident does not exist
    con.execute("INSERT INTO alerts (incident_id, channel, kind, sent_at, status) VALUES (777, 'dashboard', 'first', ?, "
                "'sent')", (T,))
    con.commit()
    con.close()
    before = rows(old_db, "alerts"), rows(old_db, "users")
    with pytest.raises(MigrationError, match="refer to a missing incident"):
        migrate(old_db)
    assert (rows(old_db, "alerts"), rows(old_db, "users")) == before    # no email column, old alerts table
    con = sqlite3.connect(old_db)
    assert OLD_CHECK in con.execute("SELECT sql FROM sqlite_master WHERE name = 'alerts'").fetchone()[0]
    assert "email" not in [r[1] for r in con.execute("PRAGMA table_info(users)")]
    con.close()


def test_migration_command_runs_twice_on_a_scratch_copy(old_db):
    env = {**os.environ, "RADS_DB_PATH": str(old_db), "RADS_DATA_DIR": str(old_db.parent)}  # both: never the real DB
    outputs = []
    for _ in range(2):
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_add_email.py")], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
        outputs.append(run.stdout)
    assert "rebuilt alerts" in outputs[0] and "nothing to do" in outputs[1]
