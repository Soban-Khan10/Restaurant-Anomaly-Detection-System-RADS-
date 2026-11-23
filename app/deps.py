"""FastAPI dependencies: DB session, current user, role checks, internal-call check, evidence folder."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import TokenError, decode_access_token
from shared.config import get_settings
from shared.contract import INTERNAL_HEADER, RoleName
from shared.db import SessionLocal

LOCALHOST = {"127.0.0.1", "::1"}


def get_evidence_dir() -> Path:
    """rads_data/evidence (overridden in tests)."""
    return get_settings().evidence_dir


def require_internal(
    request: Request,
    token: str | None = Header(default=None, alias=INTERNAL_HEADER, include_in_schema=False),
) -> None:
    """/internal/* only: caller must be on this machine and send X-RADS-Internal: <RADS_INTERNAL_TOKEN>."""
    host = request.client.host if request.client else None
    if host not in LOCALHOST:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Internal calls are accepted from 127.0.0.1 only")
    expected = get_settings().internal_token
    if not expected or not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong internal token")


def get_db() -> Iterator[Session]:
    """One session per request. Routes commit explicitly; anything uncommitted is rolled back."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    username: str
    role: RoleName
    full_name: str | None


_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not logged in or session expired",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    try:
        claims = decode_access_token(creds.credentials)
    except TokenError:
        raise _UNAUTHORIZED from None
    # Role and active flag come from the DB, so deactivation or a role change applies immediately.
    row = db.execute(
        text("SELECT u.user_id, u.username, u.full_name, u.active, r.name AS role "
             "FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.user_id = :id"),
        {"id": int(claims["sub"])},
    ).mappings().first()
    if row is None or not row["active"]:
        raise _UNAUTHORIZED
    return CurrentUser(row["user_id"], row["username"], RoleName(row["role"]), row["full_name"])


def require_role(*roles: RoleName) -> Callable[..., CurrentUser]:
    allowed = set(roles)

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is not allowed to do this")
        return user

    return checker
