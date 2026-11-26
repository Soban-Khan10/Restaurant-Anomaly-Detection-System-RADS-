"""E-mail alerts for an existing rads.db: users.email, and 'email' allowed in alerts.channel.

    python scripts/migrate_add_email.py        (uses RADS_DB_PATH from .env)

Safe to run twice: it only changes what is missing. Before changing anything it saves a copy of the database next
to it (rads.db.before-email-<time>), not in rads_data/backups, where the nightly job keeps only the last 7.

SQLite cannot change a CHECK constraint, so alerts is rebuilt in ONE transaction (SQLite's documented procedure):
foreign keys off, create alerts_new exactly as in shared/schema.sql, copy every row, drop alerts, rename alerts_new
to alerts, recreate alerts' indexes, then PRAGMA foreign_key_check(alerts) and integrity_check. If a check fails or
the row counts differ, everything is rolled back and the database is left as it was.
New databases made by scripts/init_db.py already have both changes and do not need this script.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/migrate_add_email.py`

from shared.config import SCHEMA_FILE, get_settings  # noqa: E402


class MigrationError(Exception):
    pass


def alerts_definition() -> str:
    """CREATE TABLE alerts (...) from shared/schema.sql."""
    found = re.search(r"CREATE TABLE alerts\s*\(.*?\n\);", SCHEMA_FILE.read_text(encoding="utf-8"), re.DOTALL)
    if found is None or "'email'" not in found.group(0):
        raise SystemExit(f"{SCHEMA_FILE} has no alerts table that allows 'email'; is this the right branch?")
    return found.group(0)


def _has_email_column(con: sqlite3.Connection) -> bool:
    return any(row[1] == "email" for row in con.execute("PRAGMA table_info(users)"))


def _email_channel_allowed(con: sqlite3.Connection) -> bool:
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'alerts'").fetchone()
    if sql is None:
        raise MigrationError("this database has no alerts table")
    return "'email'" in sql[0]


def _rebuild_alerts(con: sqlite3.Connection) -> None:
    indexes = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'alerts' AND sql IS NOT NULL")]
    old_cols = [r[1] for r in con.execute("PRAGMA table_info(alerts)")]
    before = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]

    con.execute(alerts_definition().replace("CREATE TABLE alerts", "CREATE TABLE alerts_new", 1))
    new_cols = {r[1] for r in con.execute("PRAGMA table_info(alerts_new)")}
    cols = ", ".join(c for c in old_cols if c in new_cols)
    con.execute(f"INSERT INTO alerts_new ({cols}) SELECT {cols} FROM alerts")
    con.execute("DROP TABLE alerts")
    con.execute("ALTER TABLE alerts_new RENAME TO alerts")
    for sql in indexes:
        con.execute(sql)

    after = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    if after != before:
        raise MigrationError(f"alerts had {before} rows before the rebuild and {after} after")
    bad_fk = con.execute("PRAGMA foreign_key_check(alerts)").fetchall()
    if bad_fk:
        raise MigrationError(f"{len(bad_fk)} alerts row(s) refer to a missing incident or user, e.g. {bad_fk[0]}")
    check = con.execute("PRAGMA integrity_check").fetchall()
    if check != [("ok",)]:
        raise MigrationError(f"integrity_check: {check[:3]}")


def migrate(db_path: Path) -> list[str]:
    """Bring db_path up to date. Returns what was done, one line per step. Raises MigrationError (after rolling
    back) if the rebuild would lose or break anything."""
    if not db_path.is_file():
        raise SystemExit(f"No database at {db_path}. A new one from scripts/init_db.py is already up to date.")
    con = sqlite3.connect(db_path, isolation_level=None)  # transactions are managed by hand below
    try:
        add_column = not _has_email_column(con)
        rebuild = not _email_channel_allowed(con)
        if not (add_column or rebuild):
            return ["users.email exists and alerts already allows 'email': nothing to do (no backup needed)"]

        backup = db_path.with_name(f"{db_path.name}.before-email-{time.strftime('%Y%m%d-%H%M%S')}")
        dst = sqlite3.connect(backup)
        try:
            con.backup(dst)  # consistent copy, WAL included
        finally:
            dst.close()
        done = [f"backup written: {backup}"]

        con.execute("PRAGMA foreign_keys = OFF")  # must be set outside a transaction; needed to drop alerts
        con.execute("BEGIN IMMEDIATE")
        try:
            if add_column:
                con.execute("ALTER TABLE users ADD COLUMN email TEXT")
                done.append("added column users.email")
            if rebuild:
                _rebuild_alerts(con)
                done.append("rebuilt alerts: channel now allows 'email' (all rows kept, checks passed)")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.execute("PRAGMA foreign_keys = ON")
        return done
    finally:
        con.close()


def main() -> int:
    db_path = get_settings().db_path
    print(f"Database: {db_path}")
    try:
        lines = migrate(db_path)
    except MigrationError as e:
        print(f"  NOT CHANGED – rolled back: {e}")
        return 1
    for line in lines:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
