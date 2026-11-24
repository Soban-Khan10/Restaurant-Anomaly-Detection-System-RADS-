"""Phase 4: /api/zones CRUD, polygon validation, rule/type consistency, audit old/new, reload."""
from __future__ import annotations

import pytest

from app_helpers import ctx  # noqa: F401  (ctx is a fixture)

SQUARE = [[100, 100], [500, 100], [500, 400], [100, 400]]


def zone_id(ctx, camera_id, ztype):
    return ctx.sql("SELECT zone_id FROM zones WHERE camera_id = :c AND type = :t", c=camera_id, t=ztype)[0]["zone_id"]


def create(ctx, user="tech", **over):
    body = {"camera_id": 2, "name": "Bin corner", "type": "waste", "polygon": SQUARE, **over}
    return ctx.client.post("/api/zones", json=body, headers=ctx.headers(user))


# ---------------------------------------------------------------- read / roles

def test_list_get_and_roles(ctx):
    h = ctx.headers("tech")
    all_zones = ctx.client.get("/api/zones", headers=h).json()
    seeded = ctx.sql("SELECT camera_id, type FROM zones ORDER BY camera_id, zone_id")
    assert [(z["camera_id"], z["type"]) for z in all_zones] == [(s["camera_id"], s["type"]) for s in seeded]
    assert (1, "prep") in [(s["camera_id"], s["type"]) for s in seeded]
    cam1 = ctx.client.get("/api/zones?camera_id=1", headers=h).json()
    assert {z["camera_id"] for z in cam1} == {1} and len(cam1) == sum(s["camera_id"] == 1 for s in seeded)
    prep1 = cam1[0]
    assert isinstance(prep1["polygon"], list) and len(prep1["rule_ids"]) == 4  # H-01, H-04, H-06, H-08
    assert ctx.client.get(f"/api/zones/{prep1['zone_id']}", headers=h).json() == prep1
    assert ctx.client.get("/api/zones/999", headers=h).status_code == 404

    m = ctx.headers("manager")
    assert ctx.client.get("/api/zones", headers=m).status_code == 403
    assert create(ctx, "manager").status_code == 403


# ---------------------------------------------------------------- create + validation

def test_create_zone(ctx):
    r = create(ctx, polygon=[[100.5, 100], [500, 100], [500, 400.25]])
    assert r.status_code == 201, r.text
    z = r.json()
    assert z["camera_id"] == 2 and z["type"] == "waste" and z["active"] is True and z["rule_ids"] == []
    assert z["polygon"] == [[100.5, 100], [500, 100], [500, 400.25]]
    assert ctx.sql("SELECT polygon FROM zones WHERE zone_id = :z", z=z["zone_id"])[0]["polygon"] == \
        "[[100.5, 100], [500, 100], [500, 400.25]]"
    assert r.headers["X-RADS-Worker-Reload"] == "ok" and ctx.worker.reloads == 1
    audit = ctx.audit("zone_create")[0]
    assert audit["details"]["new"]["polygon"] == z["polygon"] and audit["user_id"] == ctx.ids["tech"]


@pytest.mark.parametrize("polygon, reason", [
    ([[0, 0], [10, 10]], None),                                         # pydantic: < 3 points
    ([[0, 0], [100, 0], [200, 0]], "no area"),                          # on one line
    ([[0, 0], [300, 200], [300, 0], [0, 100]], "cross"),                # bow-tie
    ([[-5, 0], [100, 0], [100, 100]], "outside"),
    ([[0, 0], [1, 0]] + [[i, i * 2] for i in range(2, 101)], None),     # > 100 points
    ([[0, 0], [100, 0], "x"], None),
])
def test_polygon_validation(ctx, polygon, reason):
    r = create(ctx, polygon=polygon)
    assert r.status_code == 422
    if reason:
        assert reason in r.json()["detail"]
    assert ctx.worker.calls == []


def test_polygon_must_fit_known_frame_size(ctx):
    ctx.execute("UPDATE cameras SET frame_width = 1280, frame_height = 720 WHERE camera_id = 2")
    r = create(ctx, polygon=[[100, 100], [1300, 100], [1300, 700]])
    assert r.status_code == 422 and "1280x720" in r.json()["detail"]
    assert create(ctx, polygon=[[0, 0], [1280, 0], [1280, 720]]).status_code == 201  # edges are inside


def test_create_on_unknown_camera_and_bad_type(ctx):
    assert create(ctx, camera_id=99).status_code == 422
    assert create(ctx, type="kitchen").status_code == 422


# ---------------------------------------------------------------- update

def test_update_polygon_audit_old_new(ctx):
    zid = zone_id(ctx, 1, "waste")
    old_poly = ctx.client.get(f"/api/zones/{zid}", headers=ctx.headers("tech")).json()["polygon"]
    r = ctx.client.patch(f"/api/zones/{zid}", json={"polygon": SQUARE, "name": "Bin"}, headers=ctx.headers("admin"))
    assert r.status_code == 200 and r.json()["polygon"] == SQUARE
    assert ctx.audit("zone_update")[0]["details"] == {"name": {"old": "Waste bin", "new": "Bin"},
                                                      "polygon": {"old": old_poly, "new": SQUARE}}
    assert ctx.worker.reloads == 1


def test_type_change_must_match_rules(ctx):
    h = ctx.headers("tech")
    prep1 = zone_id(ctx, 1, "prep")  # used by H-01/H-04/H-06/H-08 rules
    r = ctx.client.patch(f"/api/zones/{prep1}", json={"type": "waste"}, headers=h)
    assert r.status_code == 409 and "needs a prep zone" in r.json()["detail"]
    new = create(ctx).json()["zone_id"]  # no rules yet
    assert ctx.client.patch(f"/api/zones/{new}", json={"type": "ignore"}, headers=h).json()["type"] == "ignore"


def test_deactivate_and_nulls(ctx):
    zid = zone_id(ctx, 2, "prep")
    h = ctx.headers("tech")
    assert ctx.client.patch(f"/api/zones/{zid}", json={"active": False}, headers=h).json()["active"] is False
    assert ctx.client.patch(f"/api/zones/{zid}", json={"polygon": None}, headers=h).status_code == 422
    assert ctx.client.patch("/api/zones/999", json={"name": "x"}, headers=h).status_code == 404


# ---------------------------------------------------------------- delete

def test_delete_rules(ctx):
    h = ctx.headers("tech")
    # Only zone of several rules -> refused.
    r = ctx.client.delete(f"/api/zones/{zone_id(ctx, 2, 'prep')}", headers=h)
    assert r.status_code == 409 and "uses only this zone" in r.json()["detail"]
    # Has incidents -> refused.
    ctx.add_incident("H-05", 1)
    assert ctx.client.delete(f"/api/zones/{zone_id(ctx, 1, 'waste')}", headers=h).status_code == 409
    # Free zone -> deleted.
    new = create(ctx).json()["zone_id"]
    assert ctx.client.delete(f"/api/zones/{new}", headers=h).status_code == 204
    assert ctx.audit("zone_delete")[0]["details"]["old"]["name"] == "Bin corner"


def test_delete_zone_removes_it_from_rules_that_have_another(ctx):
    h = ctx.headers("tech")
    extra = create(ctx, camera_id=1, type="prep", name="Prep 2").json()["zone_id"]
    rule = ctx.rule("H-01", 1)["rule_id"]
    first = zone_id(ctx, 1, "prep")
    ctx.client.patch(f"/api/rules/{rule}", json={"zone_ids": [first, extra]}, headers=h)
    assert ctx.client.delete(f"/api/zones/{extra}", headers=h).status_code == 204
    assert ctx.client.get(f"/api/rules/{rule}", headers=h).json()["zone_ids"] == [first]
    assert ctx.audit("zone_delete")[0]["details"]["removed_from_rules"] == [rule]
