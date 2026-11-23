"""Phase 1: POST /api/auth/login, GET /api/auth/me."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt

from app_helpers import PASSWORD, ctx  # noqa: F401  (ctx is a fixture)
from app.auth import JWT_ALGORITHM, decode_access_token
from shared.config import get_settings


def test_login_success_returns_8h_token_and_updates_last_login(ctx):
    r = ctx.login("manager")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "manager"
    assert body["user"]["role"] == "Manager"
    assert "password" not in str(body).lower()

    claims = decode_access_token(body["access_token"])
    assert claims["sub"] == str(ctx.ids["manager"])
    lifetime = claims["exp"] - claims["iat"]
    assert lifetime == 8 * 3600

    row = ctx.sql("SELECT last_login FROM users WHERE username = 'manager'")[0]
    assert row["last_login"] is not None and row["last_login"].endswith("+05:00")
    assert body["user"]["last_login"] == row["last_login"]


def test_login_success_writes_audit_row(ctx):
    ctx.login("tech")
    rows = ctx.audit("login")
    assert len(rows) == 1
    assert rows[0]["user_id"] == ctx.ids["tech"]
    assert rows[0]["ip_address"] == "testclient"


def test_login_wrong_password(ctx):
    r = ctx.login("admin", "wrong-password")
    assert r.status_code == 401
    rows = ctx.audit("login_failed")
    assert len(rows) == 1
    assert rows[0]["user_id"] == ctx.ids["admin"]
    assert rows[0]["details"] == {"username": "admin", "reason": "bad_credentials"}
    assert ctx.sql("SELECT last_login FROM users WHERE username = 'admin'")[0]["last_login"] is None


def test_login_unknown_user_same_message(ctx):
    bad_pw = ctx.login("admin", "wrong-password")
    unknown = ctx.login("nobody", "whatever-123")
    assert unknown.status_code == 401
    assert unknown.json() == bad_pw.json()
    rows = ctx.audit("login_failed")
    assert rows[-1]["user_id"] is None
    assert rows[-1]["details"]["username"] == "nobody"


def test_login_inactive_user_refused(ctx):
    with ctx.Session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE users SET active = 0 WHERE username = 'manager'"))
        s.commit()
    r = ctx.login("manager")
    assert r.status_code == 403
    assert ctx.audit("login_failed")[-1]["details"]["reason"] == "inactive"


def test_login_validation(ctx):
    assert ctx.client.post("/api/auth/login", json={"username": "admin"}).status_code == 422
    assert ctx.client.post("/api/auth/login", json={"username": "", "password": "x"}).status_code == 422


def test_me_returns_current_user(ctx):
    r = ctx.client.get("/api/auth/me", headers=ctx.headers("tech"))
    assert r.status_code == 200
    assert r.json()["username"] == "tech"
    assert r.json()["role"] == "Technical"
    assert "password_hash" not in r.json()


def test_me_requires_token(ctx):
    assert ctx.client.get("/api/auth/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert ctx.client.get("/api/auth/me", headers=bad).status_code == 401


def test_me_rejects_expired_token(ctx):
    past = datetime.now(timezone.utc) - timedelta(hours=9)
    token = jwt.encode({"sub": str(ctx.ids["admin"]), "role": "Admin", "iat": past,
                        "exp": past + timedelta(hours=8)}, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)
    r = ctx.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_me_rejects_token_signed_with_other_secret(ctx):
    token = jwt.encode({"sub": str(ctx.ids["admin"]), "role": "Admin",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "other-secret",
                       algorithm=JWT_ALGORITHM)
    r = ctx.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_password_hashes_are_bcrypt(ctx):
    hashes = [r["password_hash"] for r in ctx.sql("SELECT password_hash FROM users")]
    assert all(h.startswith("$2b$") for h in hashes)
    assert PASSWORD not in hashes
