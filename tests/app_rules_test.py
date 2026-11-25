"""Phase 4: /api/rules CRUD, value ranges, zone checks, one rule per hazard per camera, audit old/new, reload."""
from __future__ import annotations

import logging

import pytest

from app_helpers import ctx  # noqa: F401  (ctx is a fixture)


def zone_id(ctx, camera_id, ztype):
    return ctx.sql("SELECT zone_id FROM zones WHERE camera_id = :c AND type = :t", c=camera_id, t=ztype)[0]["zone_id"]


# ---------------------------------------------------------------- read / roles

def test_list_filters_and_get(ctx):
    h = ctx.headers("admin")
    rules = ctx.client.get("/api/rules", headers=h).json()
    assert len(rules) == 9
    cam2 = ctx.client.get("/api/rules?camera_id=2", headers=h).json()
    assert [r["hazard_id"] for r in cam2] == ["H-01", "H-04", "H-06", "H-08"]
    [h04] = ctx.client.get("/api/rules?camera_id=1&hazard_id=H-04", headers=h).json()
    assert h04["min_ratio"] == 0.3 and h04["hazard_name"] == "Phone in preparation area"
    assert h04["zone_ids"] == [zone_id(ctx, 1, "prep")]
    assert ctx.client.get(f"/api/rules/{h04['rule_id']}", headers=h).json() == h04
    assert ctx.client.get("/api/rules/999", headers=h).status_code == 404
    assert ctx.client.get("/api/rules?hazard_id=H-99", headers=h).status_code == 422


def test_manager_can_read_but_not_change_rules(ctx):
    """Managers see the Detection rules page read-only (Phase 6c)."""
    h = ctx.headers("manager")
    rid = ctx.rule("H-01")["rule_id"]
    r = ctx.client.get("/api/rules", headers=h)
    assert r.status_code == 200 and rid in [x["rule_id"] for x in r.json()]
    assert ctx.client.get(f"/api/rules/{rid}", headers=h).json()["hazard_id"] == "H-01"
    assert ctx.client.patch(f"/api/rules/{rid}", json={"window_s": 6}, headers=h).status_code == 403
    body = {"hazard_id": "H-05", "camera_id": 2, "zone_ids": [1]}
    assert ctx.client.post("/api/rules", json=body, headers=h).status_code == 403
    assert ctx.client.delete(f"/api/rules/{rid}", headers=h).status_code == 403
    assert ctx.client.get("/api/rules").status_code == 401  # still login only
    assert ctx.worker.calls == []


# ---------------------------------------------------------------- update

def test_update_values_audit_old_new_and_reload(ctx):
    rid = ctx.rule("H-01")["rule_id"]
    r = ctx.client.patch(f"/api/rules/{rid}", json={"window_s": 8, "min_ratio": 0.8, "severity": "High",
                                                     "enabled": False, "conf_threshold": 0.45},
                         headers=ctx.headers("tech"))
    assert r.status_code == 200, r.text
    assert r.json()["window_s"] == 8 and r.json()["severity"] == "High" and r.json()["enabled"] is False
    assert r.headers["X-RADS-Worker-Reload"] == "ok" and ctx.worker.reloads == 1
    audit = ctx.audit("rule_update")[0]
    assert audit["target_id"] == rid and audit["user_id"] == ctx.ids["tech"]
    assert audit["details"] == {  # unchanged conf_threshold is not listed
        "enabled": {"old": True, "new": False},
        "window_s": {"old": 5.0, "new": 8.0},
        "min_ratio": {"old": 0.7, "new": 0.8},
        "severity": {"old": "Medium", "new": "High"},
    }


@pytest.mark.parametrize("bad", [
    {"window_s": 0.5}, {"window_s": 31}, {"min_ratio": 0.05}, {"min_ratio": 1.1}, {"cooldown_s": -1},
    {"cooldown_s": 601}, {"conf_threshold": 0.99}, {"end_gap_s": 0}, {"severity": "Critical"},
    {"enabled": None}, {"zone_ids": []}, {"zone_ids": None},
])
def test_value_ranges(ctx, bad):
    rid = ctx.rule("H-01")["rule_id"]
    assert ctx.client.patch(f"/api/rules/{rid}", json=bad, headers=ctx.headers("admin")).status_code == 422
    assert ctx.worker.calls == []


def test_boundaries_accepted(ctx):
    rid = ctx.rule("H-01")["rule_id"]
    body = {"window_s": 30, "min_ratio": 1, "cooldown_s": 0, "conf_threshold": 0.05}
    assert ctx.client.patch(f"/api/rules/{rid}", json=body, headers=ctx.headers("admin")).status_code == 200


def test_zone_checks(ctx):
    h = ctx.headers("tech")
    h01 = ctx.rule("H-01", 1)["rule_id"]
    h05 = ctx.rule("H-05", 1)["rule_id"]

    r = ctx.client.patch(f"/api/rules/{h01}", json={"zone_ids": [zone_id(ctx, 1, "waste")]}, headers=h)
    assert r.status_code == 422 and "needs a prep zone" in r.json()["detail"]
    r = ctx.client.patch(f"/api/rules/{h05}", json={"zone_ids": [zone_id(ctx, 1, "prep")]}, headers=h)
    assert r.status_code == 422 and "needs a waste zone" in r.json()["detail"]
    r = ctx.client.patch(f"/api/rules/{h01}", json={"zone_ids": [zone_id(ctx, 2, "prep")]}, headers=h)
    assert r.status_code == 422 and "camera 2" in r.json()["detail"]
    assert ctx.client.patch(f"/api/rules/{h01}", json={"zone_ids": [999]}, headers=h).status_code == 422
    p = zone_id(ctx, 1, "prep")
    assert ctx.client.patch(f"/api/rules/{h01}", json={"zone_ids": [p, p]}, headers=h).status_code == 422
    assert ctx.worker.calls == []


def test_change_zones_audit(ctx):
    h = ctx.headers("tech")
    extra = ctx.client.post("/api/zones", json={"camera_id": 1, "name": "Prep 2", "type": "prep",
                                                "polygon": [[0, 0], [50, 0], [50, 50]]}, headers=h).json()["zone_id"]
    rid = ctx.rule("H-06", 1)["rule_id"]
    first = zone_id(ctx, 1, "prep")
    r = ctx.client.patch(f"/api/rules/{rid}", json={"zone_ids": [extra, first]}, headers=h)
    assert r.json()["zone_ids"] == sorted([first, extra])
    assert ctx.audit("rule_update")[0]["details"] == {"zone_ids": {"old": [first], "new": sorted([first, extra])}}


# ---------------------------------------------------------------- create / delete

def test_create_rule_and_one_per_hazard_per_camera(ctx):
    h = ctx.headers("admin")
    waste2 = ctx.client.post("/api/zones", json={"camera_id": 2, "name": "Bin 2", "type": "waste",
                                                 "polygon": [[0, 0], [90, 0], [90, 90]]}, headers=h).json()["zone_id"]
    body = {"hazard_id": "H-05", "camera_id": 2, "zone_ids": [waste2], "cooldown_s": 120}
    r = ctx.client.post("/api/rules", json=body, headers=h)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["min_ratio"] == 0.7 and rule["cooldown_s"] == 120 and rule["zone_ids"] == [waste2]
    assert ctx.audit("rule_create")[0]["details"]["new"]["hazard_id"] == "H-05"

    r = ctx.client.post("/api/rules", json=body, headers=h)
    assert r.status_code == 409 and "already has a H-05 rule" in r.json()["detail"]
    assert ctx.client.post("/api/rules", json={**body, "camera_id": 99}, headers=h).status_code == 422
    assert ctx.client.post("/api/rules", json={**body, "hazard_id": "H-02"}, headers=h).status_code == 422
    assert ctx.client.post("/api/rules", json={**body, "zone_ids": []}, headers=h).status_code == 422


def test_delete_rule(ctx):
    h = ctx.headers("tech")
    used = ctx.rule("H-01", 1)
    ctx.add_incident("H-01", 1)
    r = ctx.client.delete(f"/api/rules/{used['rule_id']}", headers=h)
    assert r.status_code == 409 and "disable it instead" in r.json()["detail"]

    free = ctx.rule("H-08", 2)["rule_id"]
    assert ctx.client.delete(f"/api/rules/{free}", headers=h).status_code == 204
    assert ctx.sql("SELECT * FROM rule_zones WHERE rule_id = :r", r=free) == []
    assert ctx.audit("rule_delete")[0]["details"]["old"]["hazard_id"] == "H-08"
    assert ctx.worker.reloads == 1


def test_worker_down_rule_still_saved(ctx, caplog):
    ctx.worker.down = True
    rid = ctx.rule("H-04", 1)["rule_id"]
    with caplog.at_level(logging.WARNING, logger="rads.app.worker"):
        r = ctx.client.patch(f"/api/rules/{rid}", json={"min_ratio": 0.4}, headers=ctx.headers("tech"))
    assert r.status_code == 200 and r.headers["X-RADS-Worker-Reload"] == "failed"
    assert ctx.rule("H-04", 1)["min_ratio"] == 0.4
    assert len(ctx.audit("rule_update")) == 1
    assert any("worker /reload failed" in m for m in caplog.messages)
