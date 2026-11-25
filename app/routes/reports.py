"""POST /api/reports (Admin/Manager), GET /api/reports, GET /api/reports/{id}/download (any logged-in user).

Files go to rads_data/reports/; reports.file_path stores the file name relative to that folder.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.auth import ROLES_REPORTS
from app.deps import CurrentUser, get_current_user, get_db, get_reports_dir, require_role
from app.reports import collect, write_report
from shared.contract import TZ

log = logging.getLogger("rads.app.reports")
router = APIRouter(prefix="/api/reports", tags=["reports"])

MAX_PERIOD_DAYS = 366
MEDIA = {"pdf": "application/pdf",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ReportIn(BaseModel):
    period_start: date = Field(description="first day included, YYYY-MM-DD")
    period_end: date = Field(description="last day included, YYYY-MM-DD")
    format: Literal["pdf", "xlsx"]

    @model_validator(mode="after")
    def _period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        if (self.period_end - self.period_start).days + 1 > MAX_PERIOD_DAYS:
            raise ValueError(f"the period can be at most {MAX_PERIOD_DAYS} days")
        return self


class ReportOut(BaseModel):
    report_id: int
    created_by: int
    created_by_name: str
    period_start: str
    period_end: str
    format: str
    file_name: str
    created_at: str
    download_url: str
    available: bool = Field(description="false if the file is no longer on disk")


_SELECT = ("SELECT r.*, COALESCE(u.full_name, u.username) AS created_by_name FROM reports r "
           "JOIN users u ON u.user_id = r.created_by")


def _out(row: dict, reports_dir: Path) -> ReportOut:
    return ReportOut(**row, file_name=row["file_path"], download_url=f"/api/reports/{row['report_id']}/download",
                     available=_safe_path(reports_dir, row["file_path"]) is not None)


def _safe_path(reports_dir: Path, rel: str) -> Path | None:
    base = reports_dir.resolve()
    p = (base / rel).resolve()
    return p if p.is_relative_to(base) and p.is_file() else None


@router.post("", response_model=ReportOut, status_code=http.HTTP_201_CREATED)
def create_report(body: ReportIn, request: Request, user: CurrentUser = Depends(require_role(*ROLES_REPORTS)),
                  db: Session = Depends(get_db), reports_dir: Path = Depends(get_reports_dir)) -> ReportOut:
    now = datetime.now(TZ)
    by = f"{user.full_name} ({user.username})" if user.full_name else user.username
    data = collect(db, body.period_start, body.period_end, by, now)

    reports_dir.mkdir(parents=True, exist_ok=True)
    name = (f"RADS_report_{body.period_start}_to_{body.period_end}_{now:%Y%m%d-%H%M%S}_"
            f"{secrets.token_hex(2)}.{body.format}")
    final = reports_dir / name
    tmp = reports_dir / f".{name}.part"
    try:
        write_report(data, body.format, tmp)
        tmp.replace(final)
        report_id = db.execute(
            text("INSERT INTO reports (created_by, period_start, period_end, format, file_path, created_at) "
                 "VALUES (:u, :s, :e, :f, :p, :t) RETURNING report_id"),
            {"u": user.user_id, "s": body.period_start.isoformat(), "e": body.period_end.isoformat(),
             "f": body.format, "p": name, "t": now.isoformat(timespec="seconds")}).scalar_one()
        write_audit(db, "report_create", user_id=user.user_id, target_type="report", target_id=report_id,
                    details={"period_start": body.period_start.isoformat(), "period_end": body.period_end.isoformat(),
                             "format": body.format, "incidents": data.total, "file": name}, request=request)
        db.commit()
    except Exception:
        tmp.unlink(missing_ok=True)
        final.unlink(missing_ok=True)  # never leave a file without its reports row
        raise
    log.info("report %s created by %s: %s (%d incidents)", report_id, user.username, name, data.total)
    row = db.execute(text(_SELECT + " WHERE r.report_id = :id"), {"id": report_id}).mappings().one()
    return _out(dict(row), reports_dir)


@router.get("", response_model=list[ReportOut])
def list_reports(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db),
                 reports_dir: Path = Depends(get_reports_dir)) -> list[ReportOut]:
    rows = db.execute(text(_SELECT + " ORDER BY r.created_at DESC, r.report_id DESC")).mappings().all()
    return [_out(dict(r), reports_dir) for r in rows]


@router.get("/{report_id}/download", response_class=FileResponse,
            responses={200: {"content": {MEDIA["pdf"]: {}, MEDIA["xlsx"]: {}}}, 404: {}})
def download_report(report_id: int, request: Request, user: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db), reports_dir: Path = Depends(get_reports_dir)) -> FileResponse:
    row = db.execute(text("SELECT file_path, format FROM reports WHERE report_id = :id"),
                     {"id": report_id}).mappings().first()
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Report not found")
    path = _safe_path(reports_dir, row["file_path"])
    if path is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "The report file is no longer on disk")
    write_audit(db, "report_download", user_id=user.user_id, target_type="report", target_id=report_id,
                request=request)
    db.commit()
    return FileResponse(path, media_type=MEDIA[row["format"]], filename=path.name,
                        headers={"Cache-Control": "private, no-store"})
