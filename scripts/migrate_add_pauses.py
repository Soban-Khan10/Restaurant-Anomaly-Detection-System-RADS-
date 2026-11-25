"""Add the monitoring_pauses table (pause monitoring) to an existing rads.db.

    python scripts/migrate_add_pauses.py        (uses RADS_DB_PATH from .env)

Safe to run twice: it only creates what is missing. Before changing anything it saves a copy of the database next
to it (rads.db.before-pauses-<time>), not in rads_data/backups, where the nightly job keeps only the last 7.
The table and index definitions are read from shared/schema.sql, so they are exactly what a new database gets.
New databases made by scripts/init_db.py already have the table and do not need this script.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/migrate_add_pauses.py`

from shared.config import SCHEMA_FILE, get_settings  # noqa: E402

TABLE = "monitoring_pauses"
INDEX = "ix_pauses_active"


def schema_statements() -> tuple[str, str]:
    """CREATE TABLE / CREATE INDEX for the pauses table, from shared/schema.sql, with IF NOT EXISTS added."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    table = re.search(rf"CREATE TABLE {TABLE}\s*\(.*?\n\);", sql, re.DOTALL)
    index = re.search(rf"CREATE INDEX {INDEX}\b[^;]*;", sql)
    if table is None or index is None:
        raise SystemExit(f"{SCHEMA_FILE} has no {TABLE} table or {INDEX} index; is this the right branch?")
    return (table.group(0).replace(f"CREATE TABLE {TABLE}", f"CREATE TABLE IF NOT EXISTS {TABLE}", 1),
            index.group(0).replace(f"CREATE INDEX {INDEX}", f"CREATE INDEX IF NOT EXISTS {INDEX}", 1))


def _exists(con: sqlite3.Connection, kind: str, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)).fetchone() is not None


def migrate(db_path: Path) -> list[str]:
    """Bring db_path up to date. Returns what was done, one line per step."""
    if not db_path.is_file():
        raise SystemExit(f"No database at {db_path}. A new one from scripts/init_db.py already has {TABLE}.")
    create_table, create_index = schema_statements()
    done: list[str] = []
    con = sqlite3.connect(db_path)
    try:
        missing_table = not _exists(con, "table", TABLE)
        missing_index = not _exists(con, "index", INDEX)
        if not (missing_table or missing_index):
            return [f"{TABLE} and {INDEX} already exist: nothing to do (no backup needed)"]

        backup = db_path.with_name(f"{db_path.name}.before-pauses-{time.strftime('%Y%m%d-%H%M%S')}")
        dst = sqlite3.connect(backup)
        try:
            con.backup(dst)  # consistent copy, WAL included
        finally:
            dst.close()
        done.append(f"backup written: {backup}")

        with con:  # one transaction
            if missing_table:
                con.execute(create_table)
                done.append(f"created table {TABLE}")
            con.execute(create_index)
            if missing_index:
                done.append(f"created index {INDEX}")
    finally:
        con.close()
    return done


def main() -> int:
    db_path = get_settings().db_path
    print(f"Database: {db_path}")
    for line in migrate(db_path):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
