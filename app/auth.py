"""Password hashing (bcrypt), JWT creation/decoding and role groups."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from shared.config import get_settings
from shared.contract import RoleName

JWT_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=8)
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_BYTES = 72  # bcrypt limit

# Role groups (design table "who may do what"). Later phases reuse these, so a policy change is one line.
ROLES_USERS = (RoleName.ADMIN,)
ROLES_REVIEW = (RoleName.ADMIN, RoleName.MANAGER)
ROLES_REPORTS = (RoleName.ADMIN, RoleName.MANAGER)
ROLES_SETTINGS = (RoleName.ADMIN, RoleName.TECHNICAL)
ROLES_LIVE_VIEW = (RoleName.ADMIN, RoleName.MANAGER, RoleName.TECHNICAL)  # open team question for Manager


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:  # malformed hash or password over 72 bytes
        return False


# Checked against when the username does not exist, so a failed login takes the same time either way.
DUMMY_HASH = hash_password("rads-dummy-password")


class TokenError(Exception):
    pass


def _secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError("RADS_JWT_SECRET is not set in .env")
    return secret


def create_access_token(user_id: int, role: str) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + TOKEN_TTL
    claims = {"sub": str(user_id), "role": role, "iat": now, "exp": expires}
    return jwt.encode(claims, _secret(), algorithm=JWT_ALGORITHM), expires


def decode_access_token(token: str) -> dict[str, Any]:
    """Return the claims; raises TokenError if the token is invalid or expired."""
    try:
        claims = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        int(claims["sub"])
    except (JWTError, KeyError, ValueError, TypeError) as e:
        raise TokenError(str(e)) from e
    return claims
