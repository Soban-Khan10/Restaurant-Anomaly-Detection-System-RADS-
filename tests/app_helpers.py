"""Shared fixtures for the app tests: a fresh temporary rads.db (from shared/schema.sql) per test.

Import the fixtures into a test module with `from app_helpers import *`.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Must be set before shared.config is imported; real env vars win over .env.
os.environ["RADS_JWT_SECRET"] = "test-secret-not-for-production-0123456789"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from shared.contract import now_iso  # noqa: E402
from shared.db import create_schema, make_engine  # noqa: E402

PASSWORD = "correct-horse-1"
USERS = {"admin": "Admin", "manager": "Manager", "tech": "Technical"}


@dataclass
class Ctx:
    client: TestClient
    Session: sessionmaker[Session]
    ids: dict[str, int]

    def sql(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self.Session() as s:
            return [dict(r) for r in s.execute(text(query), params).mappings().all()]

    def audit(self, action: str) -> list[dict[str, Any]]:
        rows = self.sql("SELECT * FROM audit_log WHERE action = :a ORDER BY log_id", a=action)
        for r in rows:
            r["details"] = json.loads(r["details"]) if r["details"] else None
        return rows

    def login(self, username: str, password: str = PASSWORD):
        return self.client.post("/api/auth/login", json={"username": username, "password": password})

    def token(self, username: str, password: str = PASSWORD) -> str:
        r = self.login(username, password)
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def headers(self, username: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(username)}"}


@pytest.fixture
def ctx(tmp_path: Path):
    db_path = tmp_path / "rads.db"
    create_schema(db_path)
    engine = make_engine(db_path)
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)

    ids: dict[str, int] = {}
    with Session_() as s:
        for role in ("Admin", "Manager", "Technical"):
            s.execute(text("INSERT INTO roles (name) VALUES (:n)"), {"n": role})
        pw_hash = hash_password(PASSWORD)
        for username, role in USERS.items():
            ids[username] = s.execute(
                text("INSERT INTO users (role_id, username, password_hash, full_name, active, created_at) "
                     "VALUES ((SELECT role_id FROM roles WHERE name = :r), :u, :h, :f, 1, :t) RETURNING user_id"),
                {"r": role, "u": username, "h": pw_hash, "f": username.title(), "t": now_iso()},
            ).scalar_one()
        s.commit()

    def _get_db():
        db = Session_()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    app.dependency_overrides[get_db] = _get_db
    try:
        yield Ctx(TestClient(app), Session_, ids)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()  # release the file so Windows can delete tmp_path
