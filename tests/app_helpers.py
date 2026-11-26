"""Shared fixtures for the app tests: a fresh temporary rads.db (from shared/schema.sql) per test.

Reference data (roles, hazards, cameras, zones, rules) is inserted with the real scripts/seed.py functions and
config/defaults.yaml, so tests see the same data as a seeded database. Plus three users: admin, manager, tech.

Import the fixture into a test module with `from app_helpers import ctx`.
"""
from __future__ import annotations

import contextlib
import io
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
os.environ["RADS_INTERNAL_TOKEN"] = "test-internal-token-0123456789"
os.environ["CAM1_RTSP"] = "rtsp://user:secret-cam-pass@192.0.2.1:554/1"
os.environ["CAM2_RTSP"] = "rtsp://user:secret-cam-pass@192.0.2.2:554/1"
# .env may hold real Gmail settings: email is off in every test (tests that need it mock SMTP).
for _name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "RADS_DASHBOARD_URL", "EMAIL_ATTACH_SNAPSHOT"):
    os.environ[_name] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.deps import get_backups_dir, get_db, get_evidence_dir, get_reports_dir  # noqa: E402
from app.main import app  # noqa: E402
from app import alerts, ws  # noqa: E402
from app.worker import WorkerClient, get_worker  # noqa: E402
from scripts.seed import seed_cameras, seed_hazards, seed_roles, seed_rules, seed_zones  # noqa: E402
from shared.config import get_settings, load_defaults  # noqa: E402
from shared.contract import INTERNAL_HEADER, now_iso  # noqa: E402
from shared.db import create_schema, make_engine  # noqa: E402

PASSWORD = "correct-horse-1"
PASSWORD_HASH = hash_password(PASSWORD)
USERS = {"admin": "Admin", "manager": "Manager", "tech": "Technical"}
INTERNAL = {INTERNAL_HEADER: get_settings().internal_token}


class WorkerStub:
    """Stands in for the worker's HTTP server in app tests. Records every call; by default /reload answers
    {"ok": true}. Set `down = True` to refuse connections, or `handler` to answer requests yourself."""

    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []
        self.down = False
        self.handler = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get(INTERNAL_HEADER)))
        if self.down:
            raise httpx.ConnectError("connection refused")
        if self.handler is not None:
            return self.handler(request)
        if request.url.path == "/reload":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    @property
    def reloads(self) -> int:
        return sum(1 for m, path, _ in self.calls if path == "/reload")


@dataclass
class Ctx:
    client: TestClient  # requests come from host "testclient" (not localhost)
    local: TestClient   # requests come from 127.0.0.1, like the worker
    Session: sessionmaker[Session]
    ids: dict[str, int]
    evidence_dir: Path
    senders: list  # fake alert senders used instead of configured WhatsApp/Telegram (empty = dashboard only)
    worker: WorkerStub
    db_path: Path

    def sql(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self.Session() as s:
            return [dict(r) for r in s.execute(text(query), params).mappings().all()]

    def execute(self, query: str, **params: Any) -> Any:
        with self.Session() as s:
            result = s.execute(text(query), params)
            value = result.scalar() if result.returns_rows else None
            s.commit()
            return value

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

    # ---- worker-side rows (what the worker would write)
    def rule(self, hazard_id: str, camera_id: int = 1) -> dict[str, Any]:
        return self.sql("SELECT r.*, (SELECT MIN(zone_id) FROM rule_zones rz WHERE rz.rule_id = r.rule_id) AS zone_id "
                        "FROM rules r WHERE hazard_id = :h AND camera_id = :c", h=hazard_id, c=camera_id)[0]

    def add_incident(self, hazard_id: str = "H-01", camera_id: int = 1, start_time: str | None = None,
                     **cols: Any) -> int:
        r = self.rule(hazard_id, camera_id)
        row = {"hazard_id": hazard_id, "rule_id": r["rule_id"], "camera_id": camera_id, "zone_id": r["zone_id"],
               "track_id": 7, "start_time": start_time or now_iso(), "conf_avg": 0.8, "conf_max": 0.9,
               "severity": r["severity"], "model_version": "test", **cols}
        names = ", ".join(row)
        return self.execute(f"INSERT INTO incidents ({names}) VALUES ({', '.join(':' + k for k in row)}) "
                            "RETURNING incident_id", **row)

    def add_evidence(self, incident_id: int, file_path: str | None = None, *, write_file: bool = True,
                     content: bytes = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9", deleted_at: str | None = None) -> int:
        rel = file_path or f"2026-12-02/cam1/{incident_id}.jpg"
        if write_file:
            p = self.evidence_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return self.execute("INSERT INTO evidence (incident_id, kind, file_path, captured_at, detections, deleted_at) "
                            "VALUES (:i, 'snapshot', :p, :t, :d, :del) RETURNING evidence_id",
                            i=incident_id, p=rel, t=now_iso(), d=json.dumps([{"class": "head_uncovered"}]),
                            **{"del": deleted_at})


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "rads.db"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    create_schema(db_path)
    engine = make_engine(db_path)
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)

    cfg = load_defaults()
    ids: dict[str, int] = {}
    with Session_() as s, contextlib.redirect_stdout(io.StringIO()):
        seed_roles(s, cfg["roles"])
        seed_hazards(s, cfg["hazards"])
        seed_cameras(s, cfg["cameras"])
        seed_zones(s, cfg["zones"])
        seed_rules(s, cfg["rules"], cfg["hazards"], cfg["cameras"])
        for username, role in USERS.items():
            ids[username] = s.execute(
                text("INSERT INTO users (role_id, username, password_hash, full_name, active, created_at) "
                     "VALUES ((SELECT role_id FROM roles WHERE name = :r), :u, :h, :f, 1, :t) RETURNING user_id"),
                {"r": role, "u": username, "h": PASSWORD_HASH, "f": username.title(), "t": now_iso()},
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
    app.dependency_overrides[get_evidence_dir] = lambda: evidence_dir
    app.dependency_overrides[get_reports_dir] = lambda: tmp_path / "reports"
    app.dependency_overrides[get_backups_dir] = lambda: tmp_path / "backups"
    ws.hub.connections.clear()
    ws.cameras.last.clear()
    ws.cameras.marked_offline.clear()
    senders: list = []
    monkeypatch.setattr(alerts, "configured_senders", lambda *a, **kw: senders)  # never call real APIs
    worker = WorkerStub()
    app.dependency_overrides[get_worker] = lambda: WorkerClient(
        "http://127.0.0.1:8001", get_settings().internal_token, transport=httpx.MockTransport(worker))
    try:
        yield Ctx(TestClient(app), TestClient(app, client=("127.0.0.1", 50000)), Session_, ids, evidence_dir,
                  senders, worker, db_path)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()  # release the file so Windows can delete tmp_path
