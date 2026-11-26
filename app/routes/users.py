"""/api/users CRUD (Admin only). Password hashes are never returned.

DELETE deactivates the user (active = 0) instead of removing the row: users are referenced by
audit_log, reports, incidents.reviewed_by and alerts, which must keep their history.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import diff, write_audit
from app.auth import PASSWORD_MAX_BYTES, PASSWORD_MIN_LEN, ROLES_USERS, hash_password
from app.deps import CurrentUser, get_db, require_role
from app.email_alerts import valid_email
from shared.contract import RoleName, now_iso

router = APIRouter(prefix="/api/users", tags=["users"])
admin_only = require_role(*ROLES_USERS)


def _check_password(pw: str) -> str:
    if len(pw) < PASSWORD_MIN_LEN:
        raise ValueError(f"password must be at least {PASSWORD_MIN_LEN} characters")
    if len(pw.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(f"password must be at most {PASSWORD_MAX_BYTES} bytes")
    return pw


Password = Annotated[str, AfterValidator(_check_password)]


def _check_email(v: str | None) -> str | None:
    """Optional: blank -> None (no email alerts); otherwise it must look like an email address."""
    if v is None or not v.strip():
        return None
    v = v.strip()
    if not valid_email(v):
        raise ValueError("enter a valid email address, for example name@example.com")
    return v


Email = Annotated[str | None, AfterValidator(_check_email)]
Username = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")]


class UserOut(BaseModel):
    user_id: int
    username: str
    role: RoleName
    full_name: str | None
    phone: str | None
    email: str | None
    active: bool
    created_at: str
    last_login: str | None


class UserCreate(BaseModel):
    username: Username
    password: Password
    role: RoleName
    full_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: Email = Field(default=None, max_length=254, description="for email alerts (Managers, Admins)")
    active: bool = True


class UserUpdate(BaseModel):
    password: Password | None = None
    role: RoleName | None = None
    full_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: Email = Field(default=None, max_length=254, description="empty or null removes it")
    active: bool | None = None


_SELECT = ("SELECT u.user_id, u.username, r.name AS role, u.full_name, u.phone, u.email, u.active, "
           "u.created_at, u.last_login FROM users u JOIN roles r ON r.role_id = u.role_id")


def fetch_user(db: Session, user_id: int) -> UserOut:
    row = db.execute(text(_SELECT + " WHERE u.user_id = :id"), {"id": user_id}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return UserOut(**row)


def _role_id(db: Session, role: RoleName) -> int:
    rid = db.execute(text("SELECT role_id FROM roles WHERE name = :n"), {"n": role.value}).scalar()
    if rid is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Role {role} missing; run scripts/seed.py")
    return rid


def _other_active_admins(db: Session, user_id: int) -> int:
    return db.execute(
        text("SELECT COUNT(*) FROM users u JOIN roles r ON r.role_id = u.role_id "
             "WHERE r.name = 'Admin' AND u.active = 1 AND u.user_id != :id"), {"id": user_id}
    ).scalar_one()


def _guard_admin_lockout(db: Session, admin: CurrentUser, target: UserOut,
                         new_role: RoleName | None, new_active: bool | None) -> None:
    """Never leave the system without an active Admin, and an Admin cannot lock themselves out."""
    loses_admin = target.role == RoleName.ADMIN and target.active and (
        (new_role is not None and new_role != RoleName.ADMIN) or new_active is False)
    if not loses_admin:
        return
    if target.user_id == admin.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove your own Admin access")
    if _other_active_admins(db, target.user_id) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one active Admin must remain")


@router.get("", response_model=list[UserOut])
def list_users(_: CurrentUser = Depends(admin_only), db: Session = Depends(get_db)) -> list[UserOut]:
    rows = db.execute(text(_SELECT + " ORDER BY u.username")).mappings().all()
    return [UserOut(**r) for r in rows]


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, _: CurrentUser = Depends(admin_only), db: Session = Depends(get_db)) -> UserOut:
    return fetch_user(db, user_id)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, admin: CurrentUser = Depends(admin_only),
                db: Session = Depends(get_db)) -> UserOut:
    if db.execute(text("SELECT 1 FROM users WHERE username = :u"), {"u": body.username}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    user_id = db.execute(
        text("INSERT INTO users (role_id, username, password_hash, full_name, phone, email, active, created_at) "
             "VALUES (:r, :u, :h, :f, :p, :e, :a, :t) RETURNING user_id"),
        {"r": _role_id(db, body.role), "u": body.username, "h": hash_password(body.password),
         "f": body.full_name, "p": body.phone, "e": body.email, "a": int(body.active), "t": now_iso()},
    ).scalar_one()
    write_audit(db, "user_create", user_id=admin.user_id, target_type="user", target_id=user_id,
                details={"username": body.username, "role": body.role.value, "full_name": body.full_name,
                         "phone": body.phone, "email": body.email, "active": body.active},
                request=request)
    db.commit()
    return fetch_user(db, user_id)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, admin: CurrentUser = Depends(admin_only),
                db: Session = Depends(get_db)) -> UserOut:
    old = fetch_user(db, user_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("password", "role", "active"):
        if required in changes and changes[required] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{required} cannot be null")
    password = changes.pop("password", None)
    _guard_admin_lockout(db, admin, old, changes.get("role"), changes.get("active"))

    sets: dict[str, object] = {}
    if "role" in changes:
        sets["role_id"] = _role_id(db, changes["role"])
    for col in ("full_name", "phone", "email"):
        if col in changes:
            sets[col] = changes[col]
    if "active" in changes:
        sets["active"] = int(changes["active"])
    if password is not None:
        sets["password_hash"] = hash_password(password)

    if sets:
        assignments = ", ".join(f"{c} = :{c}" for c in sets)  # column names come from the fixed list above
        db.execute(text(f"UPDATE users SET {assignments} WHERE user_id = :id"), {**sets, "id": user_id})

    old_vals = old.model_dump()
    details: dict[str, object] = diff(old_vals, {k: v.value if isinstance(v, RoleName) else v
                                                 for k, v in changes.items()})
    if password is not None:
        details["password"] = "changed"  # never log the password or its hash
    write_audit(db, "user_update", user_id=admin.user_id, target_type="user", target_id=user_id,
                details=details, request=request)
    db.commit()
    return fetch_user(db, user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_user(user_id: int, request: Request, admin: CurrentUser = Depends(admin_only),
                    db: Session = Depends(get_db)) -> Response:
    target = fetch_user(db, user_id)
    _guard_admin_lockout(db, admin, target, None, False)
    db.execute(text("UPDATE users SET active = 0 WHERE user_id = :id"), {"id": user_id})
    write_audit(db, "user_deactivate", user_id=admin.user_id, target_type="user", target_id=user_id,
                details={"username": target.username, "active": {"old": target.active, "new": False}},
                request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
