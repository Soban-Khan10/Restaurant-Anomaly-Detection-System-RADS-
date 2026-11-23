"""Phase 2: /internal/incidents/new, /internal/incidents/closed, /internal/heartbeat (contract section 5)."""
from __future__ import annotations

import pytest

from app.routes import internal
from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from shared.contract import INTERNAL_HEADER, now_iso

PATHS = ["/internal/incidents/new", "/internal/incidents/closed", "/internal/heartbeat"]


def _body(path, incident_id=1):
    if path.endswith("heartbeat"):
        return {"camera_id": 1, "status": "online", "fps": 4.9, "ts": now_iso()}
    return {"incident_id": incident_id}


@pytest.mark.parametrize("path", PATHS)
def test_missing_or_wrong_token_is_401(ctx, path):
    ctx.add_incident()
    assert ctx.local.post(path, json=_body(path)).status_code == 401
    assert ctx.local.post(path, json=_body(path), headers={INTERNAL_HEADER: "wrong"}).status_code == 401
    assert ctx.local.post(path, json=_body(path), headers={INTERNAL_HEADER: ""}).status_code == 401


@pytest.mark.parametrize("path", PATHS)
def test_non_localhost_is_403_even_with_token(ctx, path):
    ctx.add_incident()
    assert ctx.client.post(path, json=_body(path), headers=INTERNAL).status_code == 403


@pytest.mark.parametrize("path", PATHS[:2])
def test_incident_messages(ctx, path):
    iid = ctx.add_incident()
    r = ctx.local.post(path, json={"incident_id": iid}, headers=INTERNAL)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ctx.local.post(path, json={"incident_id": 999}, headers=INTERNAL).status_code == 404
    assert ctx.local.post(path, json={"incident_id": "x"}, headers=INTERNAL).status_code == 422
    assert ctx.local.post(path, json={"incident_id": iid, "extra": 1}, headers=INTERNAL).status_code == 422


def test_internal_routes_do_not_accept_jwt_instead_of_token(ctx):
    iid = ctx.add_incident()
    r = ctx.local.post("/internal/incidents/new", json={"incident_id": iid}, headers=ctx.headers("admin"))
    assert r.status_code == 401


def test_heartbeat_kept_in_memory_and_writes_nothing(ctx):
    body = {"camera_id": 2, "status": "reconnecting", "fps": None, "ts": "2026-12-02T14:05:31+05:00"}
    r = ctx.local.post("/internal/heartbeat", json=body, headers=INTERNAL)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert internal.LAST_HEARTBEAT[2].status == "reconnecting"
    assert ctx.sql("SELECT COUNT(*) AS n FROM camera_health")[0]["n"] == 0
    assert ctx.sql("SELECT COUNT(*) AS n FROM audit_log")[0]["n"] == 0


@pytest.mark.parametrize("bad", [
    {"status": "broken"},
    {"ts": "2026-12-02T14:05:31"},  # no UTC offset
    {"ts": "yesterday"},
    {"camera_id": None},
])
def test_heartbeat_validation(ctx, bad):
    body = {**_body("/internal/heartbeat"), **bad}
    assert ctx.local.post("/internal/heartbeat", json=body, headers=INTERNAL).status_code == 422
