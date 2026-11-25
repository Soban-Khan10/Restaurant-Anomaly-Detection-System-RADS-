"""Phase 1: /api/users CRUD (Admin only), role checks and audit rows."""
from __future__ import annotations

import pytest

from app_helpers import ctx  # noqa: F401  (ctx is a fixture)

NEW_USER = {"username": "sara", "password": "kitchen-safe-9", "role": "Manager",
            "full_name": "Sara Khan", "phone": "+923001234567"}


def _create(ctx, headers, **overrides):
    return ctx.client.post("/api/users", json={**NEW_USER, **overrides}, headers=headers)


# ---------------------------------------------------------------- role checks

@pytest.mark.parametrize("username", ["manager", "tech"])
def test_non_admin_cannot_use_any_users_route(ctx, username):
    h = ctx.headers(username)
    uid = ctx.ids["admin"]
    assert _create(ctx, h).status_code == 403
    assert ctx.client.get("/api/users", headers=h).status_code == 403
    assert ctx.client.get(f"/api/users/{uid}", headers=h).status_code == 403
    assert ctx.client.patch(f"/api/users/{uid}", json={"full_name": "x"}, headers=h).status_code == 403
    assert ctx.client.delete(f"/api/users/{uid}", headers=h).status_code == 403
    assert ctx.sql("SELECT 1 FROM users WHERE username = 'sara'") == []
    assert ctx.audit("user_create") == []


def test_users_routes_require_login(ctx):
    assert ctx.client.get("/api/users").status_code == 401
    assert ctx.client.post("/api/users", json=NEW_USER).status_code == 401


# ---------------------------------------------------------------- create

def test_admin_creates_user(ctx):
    h = ctx.headers("admin")
    r = _create(ctx, h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "sara" and body["role"] == "Manager" and body["active"] is True
    assert "password" not in body and "password_hash" not in body

    row = ctx.sql("SELECT password_hash FROM users WHERE username = 'sara'")[0]
    assert row["password_hash"].startswith("$2b$")

    audit = ctx.audit("user_create")
    assert len(audit) == 1
    assert audit[0]["user_id"] == ctx.ids["admin"]
    assert audit[0]["target_type"] == "user" and audit[0]["target_id"] == body["user_id"]
    assert audit[0]["details"]["role"] == "Manager"
    assert "kitchen-safe-9" not in str(audit[0]) and "$2b$" not in str(audit[0])

    assert ctx.login("sara", "kitchen-safe-9").status_code == 200


def test_create_duplicate_username(ctx):
    h = ctx.headers("admin")
    assert _create(ctx, h).status_code == 201
    assert _create(ctx, h).status_code == 409
    assert _create(ctx, h, username="manager").status_code == 409


@pytest.mark.parametrize("bad", [
    {"role": "Chef"},
    {"password": "short"},
    {"password": "x" * 73},
    {"username": "ab"},
    {"username": "has space"},
])
def test_create_validation(ctx, bad):
    assert _create(ctx, ctx.headers("admin"), **bad).status_code == 422


# ---------------------------------------------------------------- read

def test_list_and_get_never_return_passwords(ctx):
    h = ctx.headers("admin")
    r = ctx.client.get("/api/users", headers=h)
    assert r.status_code == 200
    assert {u["username"] for u in r.json()} == {"admin", "manager", "tech"}
    assert "password" not in r.text

    one = ctx.client.get(f"/api/users/{ctx.ids['tech']}", headers=h)
    assert one.status_code == 200 and one.json()["role"] == "Technical"
    assert "password" not in one.text
    assert ctx.client.get("/api/users/9999", headers=h).status_code == 404


# ---------------------------------------------------------------- update

def test_admin_updates_user_and_audit_stores_old_and_new(ctx):
    h = ctx.headers("admin")
    uid = ctx.ids["manager"]
    r = ctx.client.patch(f"/api/users/{uid}", json={"role": "Technical", "phone": "+92300"}, headers=h)
    assert r.status_code == 200
    assert r.json()["role"] == "Technical" and r.json()["phone"] == "+92300"

    audit = ctx.audit("user_update")[-1]
    assert audit["target_id"] == uid
    assert audit["details"] == {"role": {"old": "Manager", "new": "Technical"},
                                "phone": {"old": None, "new": "+92300"}}

    # Role change applies to existing sessions at once (role is read from the DB, not the token).
    me = ctx.client.get("/api/auth/me", headers=ctx.headers("manager"))
    assert me.json()["role"] == "Technical"


def test_password_change(ctx):
    h = ctx.headers("admin")
    uid = ctx.ids["tech"]
    r = ctx.client.patch(f"/api/users/{uid}", json={"password": "brand-new-pass"}, headers=h)
    assert r.status_code == 200
    assert ctx.login("tech").status_code == 401
    assert ctx.login("tech", "brand-new-pass").status_code == 200
    details = ctx.audit("user_update")[-1]["details"]
    assert details == {"password": "changed"}


def test_update_validation_and_missing(ctx):
    h = ctx.headers("admin")
    uid = ctx.ids["tech"]
    assert ctx.client.patch(f"/api/users/{uid}", json={"role": None}, headers=h).status_code == 422
    assert ctx.client.patch(f"/api/users/{uid}", json={"password": "short"}, headers=h).status_code == 422
    assert ctx.client.patch("/api/users/9999", json={"full_name": "x"}, headers=h).status_code == 404


# ---------------------------------------------------------------- delete (deactivate)

def test_delete_deactivates_and_revokes_access(ctx):
    tech_headers = ctx.headers("tech")
    assert ctx.client.get("/api/auth/me", headers=tech_headers).status_code == 200

    r = ctx.client.delete(f"/api/users/{ctx.ids['tech']}", headers=ctx.headers("admin"))
    assert r.status_code == 204
    assert ctx.sql("SELECT active FROM users WHERE username = 'tech'")[0]["active"] == 0

    assert ctx.client.get("/api/auth/me", headers=tech_headers).status_code == 401  # old token stops working
    assert ctx.login("tech").status_code == 403
    audit = ctx.audit("user_deactivate")
    assert len(audit) == 1 and audit[0]["target_id"] == ctx.ids["tech"]


def test_reactivate_user(ctx):
    h = ctx.headers("admin")
    ctx.client.delete(f"/api/users/{ctx.ids['tech']}", headers=h)
    r = ctx.client.patch(f"/api/users/{ctx.ids['tech']}", json={"active": True}, headers=h)
    assert r.status_code == 200 and r.json()["active"] is True
    assert ctx.login("tech").status_code == 200


# ---------------------------------------------------------------- admin lock-out protection

def test_admin_cannot_remove_own_admin_access(ctx):
    h = ctx.headers("admin")
    me = ctx.ids["admin"]
    assert ctx.client.delete(f"/api/users/{me}", headers=h).status_code == 400
    assert ctx.client.patch(f"/api/users/{me}", json={"role": "Manager"}, headers=h).status_code == 400
    assert ctx.client.patch(f"/api/users/{me}", json={"active": False}, headers=h).status_code == 400
    assert ctx.audit("user_update") == [] and ctx.audit("user_deactivate") == []


def test_admin_can_demote_another_admin_when_one_remains(ctx):
    h = ctx.headers("admin")
    r = _create(ctx, h, username="admin2", role="Admin")
    assert r.status_code == 201
    other = r.json()["user_id"]
    assert ctx.client.patch(f"/api/users/{other}", json={"role": "Manager"}, headers=h).status_code == 200
