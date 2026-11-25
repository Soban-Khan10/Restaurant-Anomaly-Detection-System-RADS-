"""Daily maintenance (03:00, APScheduler; also runnable by an Admin via POST /api/admin/maintenance/run).

1. Evidence retention: delete rads_data/evidence/YYYY-MM-DD/ folders older than RETENTION_DAYS (30) and set
   evidence.deleted_at for their rows. Database rows are NEVER deleted. Rows are only marked once their folder
   is really gone, so a folder Windows could not delete (file in use) is simply retried the next night.
2. Backup: copy rads.db to rads_data/backups/rads_YYYY-MM-DD.db with SQLite's online backup (safe while the
   worker and app are writing), keeping the newest BACKUPS_KEPT (7). A second run on the same day replaces
   that day's copy, so manual runs never push older days out.
"""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.audit import write_audit
from shared.contract import TZ, now_iso

log = logging.getLogger("rads.app.retention")

RETENTION_DAYS = 30
BACKUPS_KEPT = 7
DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BACKUP_FILE = re.compile(r"^rads_(\d{4}-\d{2}-\d{2})\.db$")


@dataclass
class RetentionResult:
    cutoff: str                                       # folders dated before this day were removed
    deleted_folders: list[str] = field(default_factory=list)
    failed_folders: list[str] = field(default_factory=list)
    evidence_rows_marked: int = 0


@dataclass
class BackupResult:
    backup_file: str | None = None
    removed_backups: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------- evidence retention

def _folder_day(p: Path) -> date | None:
    if not p.is_dir() or not DAY_DIR.match(p.name):
        return None
    try:
        return date.fromisoformat(p.name)
    except ValueError:
        return None


def run_retention(Session_: sessionmaker[Session], evidence_dir: Path, keep_days: int = RETENTION_DAYS,
                  today: date | None = None) -> RetentionResult:
    today = today or datetime.now(TZ).date()
    cutoff = today - timedelta(days=keep_days)  # a folder dated `cutoff` is exactly keep_days old: kept
    res = RetentionResult(cutoff=cutoff.isoformat())

    base = evidence_dir.resolve()
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            day = _folder_day(folder)
            if day is None or day >= cutoff:
                continue  # not a day folder, or still within the retention period
            try:
                shutil.rmtree(folder)
                res.deleted_folders.append(folder.name)
            except OSError as e:
                log.warning("retention: could not delete %s (%s); will retry next run", folder, e)
                res.failed_folders.append(folder.name)

    # Mark every not-yet-marked evidence row of an old day whose folder no longer exists (also heals rows whose
    # folder was removed by hand or by an earlier run that stopped half way).
    with Session_() as db:
        rows = db.execute(text("SELECT evidence_id, file_path FROM evidence WHERE deleted_at IS NULL "
                               "AND substr(file_path, 1, 10) < :cutoff"), {"cutoff": cutoff.isoformat()}).all()
        stamp = now_iso()
        ids = [eid for eid, rel in rows
               if DAY_DIR.match(rel[:10]) and not (base / rel[:10]).exists()]
        for eid in ids:
            db.execute(text("UPDATE evidence SET deleted_at = :t WHERE evidence_id = :id"), {"t": stamp, "id": eid})
        db.commit()
    res.evidence_rows_marked = len(ids)
    return res


# ---------------------------------------------------------------- database backup

def backup_database(db_path: Path, backups_dir: Path, keep: int = BACKUPS_KEPT,
                    today: date | None = None) -> BackupResult:
    today = today or datetime.now(TZ).date()
    res = BackupResult()
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
        target = backups_dir / f"rads_{today.isoformat()}.db"
        tmp = backups_dir / f".{target.name}.part"
        tmp.unlink(missing_ok=True)
        src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        tmp.replace(target)
        res.backup_file = target.name
    except (OSError, sqlite3.Error) as e:
        log.error("backup of %s failed: %s", db_path, e)
        res.error = f"{type(e).__name__}: {e}"
        return res

    backups = sorted((p for p in backups_dir.iterdir() if BACKUP_FILE.match(p.name)), key=lambda p: p.name)
    for old in backups[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            res.removed_backups.append(old.name)
        except OSError as e:
            log.warning("could not remove old backup %s: %s", old, e)
    return res


# ---------------------------------------------------------------- the daily job

def run_daily_maintenance(Session_: sessionmaker[Session], evidence_dir: Path, db_path: Path, backups_dir: Path,
                          user_id: int | None = None, today: date | None = None) -> dict[str, Any]:
    """Retention + backup + one audit_log row (`maintenance_run`; user_id NULL when run by the scheduler).
    Never raises, so a failure cannot stop the scheduler."""
    out: dict[str, Any] = {}
    try:
        out["retention"] = asdict(run_retention(Session_, evidence_dir, today=today))
    except Exception as e:  # noqa: BLE001
        log.exception("evidence retention failed")
        out["retention"] = {"error": f"{type(e).__name__}: {e}"}
    out["backup"] = asdict(backup_database(db_path, backups_dir, today=today))
    try:
        with Session_() as db:
            write_audit(db, "maintenance_run", user_id=user_id, target_type="system", details=out)
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not write the maintenance audit row")
    r, b = out["retention"], out["backup"]
    log.info("maintenance: %d evidence folder(s) deleted, %d row(s) marked, backup %s",
             len(r.get("deleted_folders", [])), r.get("evidence_rows_marked", 0), b.get("backup_file") or "FAILED")
    return out
