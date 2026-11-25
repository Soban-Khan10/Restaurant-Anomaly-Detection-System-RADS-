"""/api/rules: every logged-in user may read (the dashboard shows Managers a read-only page); only Admin and
Technical may create, change or delete. One rule per hazard per camera.

Validation: conf_threshold 0.05–0.95, window 1–30 s, min_ratio 0.1–1, end_gap 1–120 s, cooldown 0–600 s,
severity Low/Medium/High; at least one zone, all on the rule's camera, of the type the hazard needs
(H-05 -> waste, others -> prep). Ignore zones can never be attached to a rule. Audit rows store old and new values (including zone ids).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import diff, write_audit
from app.auth import ROLES_SETTINGS
from app.deps import CurrentUser, get_current_user, get_db, require_role
from app.routes.zones import required_zone_type
from app.worker import WorkerClient, get_worker, reload_after_save
from shared.contract import HazardId, Severity, ZoneType

router = APIRouter(prefix="/api/rules", tags=["rules"])
settings_role = require_role(*ROLES_SETTINGS)

END_GAP_MAX_S = 120  # H-04 (phone lying still) uses 60
VALUE_FIELDS = ("enabled", "conf_threshold", "window_s", "min_ratio", "end_gap_s", "cooldown_s", "severity")


def _unique(v: list[int] | None) -> list[int] | None:
    if v is not None and len(set(v)) != len(v):
        raise ValueError("zone ids must not repeat")
    return v


class RuleValues(BaseModel):
    enabled: bool | None = None
    conf_threshold: float | None = Field(None, ge=0.05, le=0.95, description="minimum detection confidence")
    window_s: float | None = Field(None, ge=1, le=30, description="seconds looked at before deciding")
    min_ratio: float | None = Field(None, ge=0.1, le=1, description="share of the window the violation must be seen")
    end_gap_s: float | None = Field(None, ge=1, le=END_GAP_MAX_S,
                                    description="seconds without the violation that end it")
    cooldown_s: float | None = Field(None, ge=0, le=600, description="quiet time after an incident")
    severity: Severity | None = None
    zone_ids: list[int] | None = Field(None, min_length=1, max_length=20)

    _zones = field_validator("zone_ids")(_unique)


class RuleCreate(RuleValues):
    hazard_id: HazardId
    camera_id: int
    enabled: bool = True
    conf_threshold: float = Field(0.45, ge=0.05, le=0.95)
    window_s: float = Field(5, ge=1, le=30)
    min_ratio: float = Field(0.70, ge=0.1, le=1)
    end_gap_s: float = Field(5, ge=1, le=END_GAP_MAX_S)
    cooldown_s: float = Field(60, ge=0, le=600)
    severity: Severity = Severity.MEDIUM
    zone_ids: list[int] = Field(min_length=1, max_length=20)


class RuleOut(BaseModel):
    rule_id: int
    hazard_id: str
    hazard_name: str
    camera_id: int
    enabled: bool
    conf_threshold: float
    window_s: float
    min_ratio: float
    end_gap_s: float
    cooldown_s: float
    severity: str
    zone_ids: list[int]


# ---------------------------------------------------------------- helpers

def _row(db: Session, rule_id: int) -> dict[str, Any]:
    row = db.execute(text("SELECT r.*, h.name AS hazard_name FROM rules r JOIN hazards h USING (hazard_id) "
                          "WHERE r.rule_id = :id"), {"id": rule_id}).mappings().first()
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Rule not found")
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    out["zone_ids"] = [r[0] for r in db.execute(
        text("SELECT zone_id FROM rule_zones WHERE rule_id = :r ORDER BY zone_id"), {"r": rule_id})]
    return out


def _check_zones(db: Session, hazard_id: str, camera_id: int, zone_ids: list[int]) -> None:
    need = required_zone_type(hazard_id)
    rows = {r.zone_id: r for r in db.execute(
        text(f"SELECT zone_id, camera_id, type, name FROM zones WHERE zone_id IN "
             f"({', '.join(str(int(z)) for z in zone_ids)})"))}
    for z in zone_ids:
        if z not in rows:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"Zone {z} does not exist")
        if rows[z].camera_id != camera_id:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"Zone '{rows[z].name}' is on camera {rows[z].camera_id}, not camera {camera_id}")
        if rows[z].type == ZoneType.IGNORE:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"'{rows[z].name}' is an ignored area; ignored areas cannot be used by a rule")
        if rows[z].type != need.value:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"{hazard_id} needs a {need.value} zone; '{rows[z].name}' is a {rows[z].type} zone")


def _set_zones(db: Session, rule_id: int, zone_ids: list[int]) -> None:
    db.execute(text("DELETE FROM rule_zones WHERE rule_id = :r"), {"r": rule_id})
    for z in zone_ids:
        db.execute(text("INSERT INTO rule_zones (rule_id, zone_id) VALUES (:r, :z)"), {"r": rule_id, "z": z})


def _audit_values(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("hazard_id", "camera_id", *VALUE_FIELDS, "zone_ids")}


# ---------------------------------------------------------------- routes

@router.get("", response_model=list[RuleOut])
def list_rules(camera_id: int | None = Query(None), hazard_id: HazardId | None = Query(None),
               _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[RuleOut]:
    where, params = [], {}
    if camera_id is not None:
        where.append("camera_id = :c")
        params["c"] = camera_id
    if hazard_id is not None:
        where.append("hazard_id = :h")
        params["h"] = hazard_id.value
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    ids = [r[0] for r in db.execute(text(f"SELECT rule_id FROM rules{clause} ORDER BY camera_id, hazard_id"), params)]
    return [RuleOut(**_row(db, i)) for i in ids]


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: int, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> RuleOut:
    return RuleOut(**_row(db, rule_id))


@router.post("", response_model=RuleOut, status_code=http.HTTP_201_CREATED)
def create_rule(body: RuleCreate, request: Request, response: Response, user: CurrentUser = Depends(settings_role),
                db: Session = Depends(get_db), worker: WorkerClient = Depends(get_worker)) -> RuleOut:
    if db.execute(text("SELECT 1 FROM cameras WHERE camera_id = :c"), {"c": body.camera_id}).first() is None:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"Camera {body.camera_id} does not exist")
    existing = db.execute(text("SELECT rule_id FROM rules WHERE hazard_id = :h AND camera_id = :c"),
                          {"h": body.hazard_id.value, "c": body.camera_id}).scalar()
    if existing is not None:
        raise HTTPException(http.HTTP_409_CONFLICT,
                            f"Camera {body.camera_id} already has a {body.hazard_id.value} rule (rule_id {existing}); "
                            f"change it with PATCH /api/rules/{existing}")
    _check_zones(db, body.hazard_id.value, body.camera_id, body.zone_ids)
    v = body.model_dump()
    rule_id = db.execute(
        text("INSERT INTO rules (hazard_id, camera_id, enabled, conf_threshold, window_s, min_ratio, end_gap_s, "
             "cooldown_s, severity) VALUES (:hazard_id, :camera_id, :enabled, :conf_threshold, :window_s, "
             ":min_ratio, :end_gap_s, :cooldown_s, :severity) RETURNING rule_id"),
        {**v, "hazard_id": body.hazard_id.value, "severity": body.severity.value,
         "enabled": int(body.enabled)}).scalar_one()
    _set_zones(db, rule_id, body.zone_ids)
    new = _row(db, rule_id)
    write_audit(db, "rule_create", user_id=user.user_id, target_type="rule", target_id=rule_id,
                details={"new": _audit_values(new)}, request=request)
    db.commit()
    reload_after_save(worker, response)
    return RuleOut(**new)


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleValues, request: Request, response: Response,
                user: CurrentUser = Depends(settings_role), db: Session = Depends(get_db),
                worker: WorkerClient = Depends(get_worker)) -> RuleOut:
    """Change values and/or zones. Hazard and camera cannot change: create a new rule instead."""
    old = _row(db, rule_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if v is None:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"{k} cannot be null")
    zone_ids = changes.pop("zone_ids", None)
    if zone_ids is not None:
        _check_zones(db, old["hazard_id"], old["camera_id"], zone_ids)
        _set_zones(db, rule_id, zone_ids)
    if changes:
        values = {k: (int(v) if k == "enabled" else v.value if isinstance(v, Severity) else v)
                  for k, v in changes.items()}
        db.execute(text(f"UPDATE rules SET {', '.join(f'{k} = :{k}' for k in values)} WHERE rule_id = :id"),
                   {**values, "id": rule_id})  # keys come from the pydantic model, not the client
    new = _row(db, rule_id)
    write_audit(db, "rule_update", user_id=user.user_id, target_type="rule", target_id=rule_id,
                details=diff(_audit_values(old), _audit_values(new)), request=request)
    db.commit()
    reload_after_save(worker, response)
    return RuleOut(**new)


@router.delete("/{rule_id}", status_code=http.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, request: Request, user: CurrentUser = Depends(settings_role),
                db: Session = Depends(get_db), worker: WorkerClient = Depends(get_worker)) -> Response:
    old = _row(db, rule_id)
    if db.execute(text("SELECT 1 FROM incidents WHERE rule_id = :r LIMIT 1"), {"r": rule_id}).first():
        raise HTTPException(http.HTTP_409_CONFLICT, "This rule has incidents; disable it instead (enabled = false)")
    try:
        db.execute(text("DELETE FROM rules WHERE rule_id = :r"), {"r": rule_id})  # rule_zones: ON DELETE CASCADE
    except IntegrityError:  # the worker recorded an incident for it after the check above
        db.rollback()
        raise HTTPException(http.HTTP_409_CONFLICT,
                            "This rule has incidents; disable it instead (enabled = false)") from None
    write_audit(db, "rule_delete", user_id=user.user_id, target_type="rule", target_id=rule_id,
                details={"old": _audit_values(old)}, request=request)
    db.commit()
    response = Response(status_code=http.HTTP_204_NO_CONTENT)
    reload_after_save(worker, response)
    return response
