"""Create rads_data/ and an empty rads.db from shared/schema.sql.

    python scripts/init_db.py           # refuses if the database already exists
    python scripts/init_db.py --force   # deletes the existing database and recreates it
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/init_db.py`

from shared.config import get_settings  # noqa: E402
from shared.db import create_schema  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the RADS SQLite database.")
    parser.add_argument("--force", action="store_true",
                        help="delete an existing database first (ALL DATA IS LOST)")
    args = parser.parse_args()

    s = get_settings()
    for d in (s.data_dir, s.evidence_dir, s.reports_dir, s.logs_dir, s.db_path.parent):
        d.mkdir(parents=True, exist_ok=True)

    if s.db_path.exists():
        if not args.force:
            print(f"Database already exists: {s.db_path}\nUse --force to delete and recreate it.")
            return 1
        for suffix in ("", "-wal", "-shm"):
            Path(f"{s.db_path}{suffix}").unlink(missing_ok=True)
        print(f"Deleted old database: {s.db_path}")

    create_schema(s.db_path)

    con = sqlite3.connect(str(s.db_path))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        con.close()

    print(f"Created {s.db_path}  (journal_mode={mode})")
    print(f"{len(tables)} tables: {', '.join(tables)}")
    print("Next: python scripts/seed.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
