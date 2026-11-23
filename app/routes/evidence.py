"""GET /api/evidence/{evidence_id}/image – logged-in users only; every view writes audit_log `view_evidence`."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.deps import CurrentUser, get_current_user, get_db, get_evidence_dir

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}


@router.get("/{evidence_id}/image", response_class=FileResponse,
            responses={200: {"content": {"image/jpeg": {}}}, 404: {}, 410: {"description": "Deleted by retention"}})
def evidence_image(evidence_id: int, request: Request,
                   user: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db),
                   evidence_dir: Path = Depends(get_evidence_dir)) -> FileResponse:
    row = db.execute(text("SELECT incident_id, file_path, deleted_at FROM evidence WHERE evidence_id = :id"),
                     {"id": evidence_id}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    if row["deleted_at"] is not None:
        raise HTTPException(status.HTTP_410_GONE, "Evidence was deleted by the retention policy")

    base = evidence_dir.resolve()
    path = (base / row["file_path"]).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence file not found")

    write_audit(db, "view_evidence", user_id=user.user_id, target_type="evidence", target_id=evidence_id,
                details={"incident_id": row["incident_id"]}, request=request)
    db.commit()
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                        headers={"Cache-Control": "private, no-store"})
