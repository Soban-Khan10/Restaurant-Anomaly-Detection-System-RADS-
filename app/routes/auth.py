"""POST /api/auth/login, GET /api/auth/me."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.auth import DUMMY_HASH, create_access_token, verify_password
from app.deps import CurrentUser, get_current_user, get_db
from app.routes.users import UserOut, fetch_user
from shared.contract import now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> LoginOut:
    username = body.username.strip()
    row = db.execute(
        text("SELECT user_id, password_hash, active FROM users WHERE username = :u"), {"u": username}
    ).mappings().first()

    ok = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if row is None or not ok:
        write_audit(db, "login_failed", user_id=row["user_id"] if row else None,
                    details={"username": username, "reason": "bad_credentials"}, request=request)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")

    if not row["active"]:
        write_audit(db, "login_failed", user_id=row["user_id"],
                    details={"username": username, "reason": "inactive"}, request=request)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")

    user_id = row["user_id"]
    db.execute(text("UPDATE users SET last_login = :t WHERE user_id = :id"), {"t": now_iso(), "id": user_id})
    write_audit(db, "login", user_id=user_id, target_type="user", target_id=user_id, request=request)
    db.commit()

    user = fetch_user(db, user_id)
    token, expires = create_access_token(user_id, user.role)
    return LoginOut(access_token=token, expires_at=expires, user=user)


@router.get("/me", response_model=UserOut)
def me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    return fetch_user(db, current.user_id)
