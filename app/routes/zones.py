"""/api/zones CRUD (Admin/Technical). Polygons are [[x, y], ...] in camera pixels.

Validation: at least 3 points (at most 100), inside the camera's frame size when it is known (else x, y >= 0),
non-zero area, edges must not cross. Audit rows store old and new values.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import diff, write_audit
from app.auth import ROLES_SETTINGS
from app.deps import CurrentUser, get_db, require_role
from app.worker import WorkerClient, get_worker, reload_after_save
from shared.config import load_defaults
from shared.contract import ZoneType

router = APIRouter(prefix="/api/zones", tags=["zones"])
settings_role = require_role(*ROLES_SETTINGS)

MAX_POINTS = 100
Point = tuple[float, float]


# ---------------------------------------------------------------- polygon checks

def _segments_cross(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    def orient(a: Point, b: Point, c: Point) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a: Point, b: Point, c: Point) -> bool:
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return any(d == 0 and on_segment(a, b, c) for d, a, b, c in
               ((d1, q1, q2, p1), (d2, q1, q2, p2), (d3, p1, p2, q1), (d4, p1, p2, q2)))


def validate_polygon(points: list[list[float]], width: int | None, height: int | None) -> None:
    """Raise HTTP 422 with a plain-language reason if the polygon is unusable."""
    def bad(msg: str) -> HTTPException:
        return HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"Polygon: {msg}")

    if len(points) < 3:
        raise bad("needs at least 3 points")
    if len(points) > MAX_POINTS:
        raise bad(f"at most {MAX_POINTS} points")
    pts: list[Point] = [(float(p[0]), float(p[1])) for p in points]
    for x, y in pts:
        if x < 0 or y < 0:
            raise bad(f"point ({x:g}, {y:g}) is outside the picture")
        if width is not None and height is not None and (x > width or y > height):
            raise bad(f"point ({x:g}, {y:g}) is outside the {width}x{height} camera picture")
    area = sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts))) / 2
    if abs(area) < 1:
        raise bad("has no area (points are on one line or repeated)")
    n = len(pts)
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # neighbouring edges share a corner
            if _segments_cross(*edges[i], *edges[j]):
                raise bad("edges cross each other; draw the outline in order around the area")


def required_zone_type(hazard_id: str) -> ZoneType:
    """H-05 -> waste, others -> prep (from config/defaults.yaml rules.zone_type)."""
    mapping = load_defaults()["rules"]["zone_type"]
    if hazard_id not in mapping:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown hazard {hazard_id}")
    return ZoneType(mapping[hazard_id])


# ---------------------------------------------------------------- models

Polygon = list[list[float]]
PolygonField = Field(min_length=3, max_length=MAX_POINTS, description="[[x, y], ...] in camera pixels")


class ZoneCreate(BaseModel):
    camera_id: int
    name: str = Field(min_length=1, max_length=60)
    type: ZoneType
    polygon: list[tuple[float, float]] = PolygonField
    active: bool = True


class ZoneUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    type: ZoneType | None = None
    polygon: list[tuple[float, float]] | None = Field(None, min_length=3, max_length=MAX_POINTS)
    active: bool | None = None


class ZoneOut(BaseModel):
    zone_id: int
    camera_id: int
    name: str
    type: ZoneType
    polygon: Polygon
    active: bool
    rule_ids: list[int]


# ---------------------------------------------------------------- helpers

def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else v


def _poly_json(points: list[tuple[float, float]]) -> str:
    return json.dumps([[_num(x), _num(y)] for x, y in points])


def _row(db: Session, zone_id: int) -> dict[str, Any]:
    row = db.execute(text("SELECT * FROM zones WHERE zone_id = :id"), {"id": zone_id}).mappings().first()
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Zone not found")
    out = dict(row)
    out["polygon"] = json.loads(out["polygon"])
    out["active"] = bool(out["active"])
    out["rule_ids"] = [r[0] for r in db.execute(
        text("SELECT rule_id FROM rule_zones WHERE zone_id = :z ORDER BY rule_id"), {"z": zone_id})]
    return out


def _camera_size(db: Session, camera_id: int) -> tuple[int | None, int | None]:
    row = db.execute(text("SELECT frame_width, frame_height FROM cameras WHERE camera_id = :c"),
                     {"c": camera_id}).first()
    if row is None:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"Camera {camera_id} does not exist")
    return row[0], row[1]


ZONE_IN_USE = ("Incidents refer to this zone, so it cannot be deleted; deactivate it instead "
               "(PATCH the zone with active = false)")


def _has_incidents(db: Session, zone_id: int) -> bool:
    return db.execute(text("SELECT 1 FROM incidents WHERE zone_id = :z LIMIT 1"), {"z": zone_id}).first() is not None


def _audit_values(z: dict[str, Any]) -> dict[str, Any]:
    return {k: z[k] for k in ("camera_id", "name", "type", "polygon", "active")}


# ---------------------------------------------------------------- routes

@router.get("", response_model=list[ZoneOut])
def list_zones(camera_id: int | None = Query(None), _: CurrentUser = Depends(settings_role),
               db: Session = Depends(get_db)) -> list[ZoneOut]:
    where, params = ("WHERE camera_id = :c", {"c": camera_id}) if camera_id is not None else ("", {})
    ids = [r[0] for r in db.execute(text(f"SELECT zone_id FROM zones {where} ORDER BY camera_id, zone_id"), params)]
    return [ZoneOut(**_row(db, i)) for i in ids]


@router.get("/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: int, _: CurrentUser = Depends(settings_role), db: Session = Depends(get_db)) -> ZoneOut:
    return ZoneOut(**_row(db, zone_id))


@router.post("", response_model=ZoneOut, status_code=http.HTTP_201_CREATED)
def create_zone(body: ZoneCreate, request: Request, response: Response, user: CurrentUser = Depends(settings_role),
                db: Session = Depends(get_db), worker: WorkerClient = Depends(get_worker)) -> ZoneOut:
    validate_polygon([list(p) for p in body.polygon], *_camera_size(db, body.camera_id))
    zone_id = db.execute(
        text("INSERT INTO zones (camera_id, name, type, polygon, active) VALUES (:c, :n, :t, :p, :a) "
             "RETURNING zone_id"),
        {"c": body.camera_id, "n": body.name, "t": body.type.value, "p": _poly_json(body.polygon),
         "a": int(body.active)}).scalar_one()
    new = _row(db, zone_id)
    write_audit(db, "zone_create", user_id=user.user_id, target_type="zone", target_id=zone_id,
                details={"new": _audit_values(new)}, request=request)
    db.commit()
    reload_after_save(worker, response)
    return ZoneOut(**new)


@router.patch("/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, body: ZoneUpdate, request: Request, response: Response,
                user: CurrentUser = Depends(settings_role), db: Session = Depends(get_db),
                worker: WorkerClient = Depends(get_worker)) -> ZoneOut:
    old = _row(db, zone_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if v is None:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, f"{k} cannot be null")

    if "type" in changes and changes["type"] != old["type"]:
        for rule_id, hazard_id in db.execute(text(
                "SELECT r.rule_id, r.hazard_id FROM rule_zones rz JOIN rules r ON r.rule_id = rz.rule_id "
                "WHERE rz.zone_id = :z"), {"z": zone_id}):
            if required_zone_type(hazard_id) != changes["type"]:
                raise HTTPException(http.HTTP_409_CONFLICT,
                                    f"Rule {rule_id} ({hazard_id}) needs a {required_zone_type(hazard_id).value} "
                                    "zone; remove this zone from that rule first")
    values: dict[str, Any] = {}
    if "polygon" in changes:
        validate_polygon([list(p) for p in changes["polygon"]], *_camera_size(db, old["camera_id"]))
        values["polygon"] = _poly_json(changes["polygon"])
    if "name" in changes:
        values["name"] = changes["name"]
    if "type" in changes:
        values["type"] = changes["type"].value
    if "active" in changes:
        values["active"] = int(changes["active"])
    if values:
        db.execute(text(f"UPDATE zones SET {', '.join(f'{k} = :{k}' for k in values)} WHERE zone_id = :id"),
                   {**values, "id": zone_id})
    new = _row(db, zone_id)
    write_audit(db, "zone_update", user_id=user.user_id, target_type="zone", target_id=zone_id,
                details=diff(_audit_values(old), _audit_values(new)), request=request)
    db.commit()
    reload_after_save(worker, response)
    return ZoneOut(**new)


@router.delete("/{zone_id}", status_code=http.HTTP_204_NO_CONTENT)
def delete_zone(zone_id: int, request: Request, user: CurrentUser = Depends(settings_role),
                db: Session = Depends(get_db), worker: WorkerClient = Depends(get_worker)) -> Response:
    old = _row(db, zone_id)
    if _has_incidents(db, zone_id):
        raise HTTPException(http.HTTP_409_CONFLICT, ZONE_IN_USE)
    for (rule_id,) in db.execute(text(
            "SELECT rz.rule_id FROM rule_zones rz WHERE rz.zone_id = :z AND "
            "(SELECT COUNT(*) FROM rule_zones x WHERE x.rule_id = rz.rule_id) = 1"), {"z": zone_id}):
        raise HTTPException(http.HTTP_409_CONFLICT,
                            f"Rule {rule_id} uses only this zone; give the rule another zone or delete it first")
    try:
        db.execute(text("DELETE FROM zones WHERE zone_id = :z"), {"z": zone_id})  # rule_zones: ON DELETE CASCADE
    except IntegrityError:  # incidents.zone_id has no cascade: the worker recorded one after the check above
        db.rollback()
        raise HTTPException(http.HTTP_409_CONFLICT, ZONE_IN_USE) from None
    write_audit(db, "zone_delete", user_id=user.user_id, target_type="zone", target_id=zone_id,
                details={"old": _audit_values(old), "removed_from_rules": old["rule_ids"]}, request=request)
    db.commit()
    response = Response(status_code=http.HTTP_204_NO_CONTENT)
    reload_after_save(worker, response)
    return response
