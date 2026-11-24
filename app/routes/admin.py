"""POST /api/admin/maintenance/run – run the daily 03:00 job now (Admin only; for testing and after outages)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.deps import CurrentUser, get_backups_dir, get_db, get_evidence_dir, require_role
from app.retention import run_daily_maintenance
from shared.contract import RoleName

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/maintenance/run")
async def run_maintenance(user: CurrentUser = Depends(require_role(RoleName.ADMIN)), db: Session = Depends(get_db),
                          evidence_dir: Path = Depends(get_evidence_dir),
                          backups_dir: Path = Depends(get_backups_dir)) -> dict[str, Any]:
    """Evidence retention (folders older than 30 days) + database backup, exactly as the nightly job does.
    Returns what was deleted, how many evidence rows were marked, and the backup file name."""
    engine = db.get_bind()
    db.close()
    return await run_in_threadpool(run_daily_maintenance, sessionmaker(bind=engine), evidence_dir,
                                   Path(engine.url.database), backups_dir, user.user_id)
