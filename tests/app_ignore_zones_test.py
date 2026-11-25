"""Zone / rule checks agreed with Member A: ignore zones, frame-edge polygons (2688x1520), end_gap up to 120 s,
the new H-04 values, safe zone deletion, and fake incidents never using an ignore zone."""
from __future__ import annotations

import random

import pytest

from app.routes import zones as zone_routes
from app_helpers import ctx  # noqa: F401  (ctx is a fixture)
from scripts.fake_incidents import FakeWorker

W, H = 2688, 1520
FULL = [[0, 0], [W, 0], [W, H], [0, H]]
OUTSIDE = [[1600, 0], [2545, 0], [2535, 220], [2486, 555], [2461, 657], [2438, 676], [2362, 655], [2273, 628],
           [2183, 580], [2080, 555], [1755, 462], [1600, 413]]  # camera 2 customer side, as agreed with Member A


@pytest.fixture
def cams(ctx):
    ctx.execute("UPDATE cameras SET frame_width = :w, frame_height = :h", w=W, h=H)
    return ctx


def zone_id(ctx, camera_id, ztype):
    return ctx.sql("SELECT zone_id FROM zones WHERE camera_id = :c AND type = :t", c=camera_id, t=ztype)[0]["zone_id"]


def add_ignore(ctx, user="tech"):
    r = ctx.client.post("/api/zones", json={"camera_id": 2, "name": "Outside (customer side)", "type": "ignore",
                                            "polygon": OUTSIDE}, headers=ctx.headers(user))
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- 1. zone types

@pytest.mark.parametrize("ztype", ["prep", "waste", "ignore"])
def test_all_three_zone_types_can_be_created(cams, ztype):
    r = cams.client.post("/api/zones", json={"camera_id": 2, "name": f"z-{ztype}", "type": ztype,
                                             "polygon": [[10, 10], [500, 10], [500, 400]]},
                         headers=cams.headers("tech"))
    assert r.status_code == 201 and r.json()["type"] == ztype


def test_ignore_zone_listed_and_reload_called(cams):
    z = add_ignore(cams)
    assert z["polygon"] == OUTSIDE and z["rule_ids"] == []
    listed = cams.client.get("/api/zones?camera_id=2", headers=cams.headers("tech")).json()
    assert {x["zone_id"]: x["type"] for x in listed}[z["zone_id"]] == "ignore"
    assert "prep" in [x["type"] for x in listed]
    assert cams.worker.reloads == 1


# ---------------------------------------------------------------- 2. polygons inside the frame, edge included

def test_full_frame_polygon_on_the_edge_is_accepted(cams):
    h = cams.headers("tech")
    r = cams.client.post("/api/zones", json={"camera_id": 1, "name": "Whole picture", "type": "prep",
                                             "polygon": FULL}, headers=h)
    assert r.status_code == 201, r.text
    prep2 = zone_id(cams, 2, "prep")
    assert cams.client.patch(f"/api/zones/{prep2}", json={"polygon": FULL}, headers=h).status_code == 200


@pytest.mark.parametrize("bad_point", [[W + 1, 0], [0, H + 1], [-1, 5], [W, H + 0.5]])
def test_point_beyond_the_frame_is_rejected(cams, bad_point):
    poly = [[0, 0], bad_point, [W, H], [0, H]]
    r = cams.client.post("/api/zones", json={"camera_id": 1, "name": "x", "type": "prep", "polygon": poly},
                         headers=cams.headers("tech"))
    assert r.status_code == 422 and "outside" in r.json()["detail"]


def test_fewer_than_three_points_rejected(cams):
    r = cams.client.post("/api/zones", json={"camera_id": 1, "name": "x", "type": "prep",
                                             "polygon": [[0, 0], [W, H]]}, headers=cams.headers("tech"))
    assert r.status_code == 422


def test_frame_size_cannot_shrink_below_full_frame_zones(cams):
    h = cams.headers("tech")
    cams.client.patch(f"/api/zones/{zone_id(cams, 1, 'prep')}", json={"polygon": FULL}, headers=h)
    r = cams.client.patch("/api/cameras/1", json={"frame_width": 1920, "frame_height": 1080}, headers=h)
    assert r.status_code == 409
    assert cams.client.patch("/api/cameras/1", json={"frame_width": W, "frame_height": H},
                             headers=h).status_code == 200


# ---------------------------------------------------------------- 3. ignore zones never on a rule

def test_ignore_zone_cannot_be_attached_to_a_rule(cams):
    ign = add_ignore(cams)["zone_id"]
    h = cams.headers("tech")
    for hazard in ("H-01", "H-04", "H-06", "H-08"):
        rid = cams.rule(hazard, 2)["rule_id"]
        r = cams.client.patch(f"/api/rules/{rid}", json={"zone_ids": [zone_id(cams, 2, "prep"), ign]}, headers=h)
        assert r.status_code == 422 and "ignored area" in r.json()["detail"]
    r = cams.client.post("/api/rules", json={"hazard_id": "H-05", "camera_id": 2, "zone_ids": [ign]}, headers=h)
    assert r.status_code == 422 and "ignored area" in r.json()["detail"]
    assert cams.sql("SELECT * FROM rule_zones WHERE zone_id = :z", z=ign) == []


def test_zone_used_by_a_rule_cannot_become_ignore(cams):
    prep1 = zone_id(cams, 1, "prep")
    r = cams.client.patch(f"/api/zones/{prep1}", json={"type": "ignore"}, headers=cams.headers("tech"))
    assert r.status_code == 409 and "needs a prep zone" in r.json()["detail"]


def test_type_match_unchanged(cams):
    h = cams.headers("tech")
    h05 = cams.rule("H-05", 1)["rule_id"]
    assert cams.client.patch(f"/api/rules/{h05}", json={"zone_ids": [zone_id(cams, 1, "prep")]},
                             headers=h).status_code == 422
    h01 = cams.rule("H-01", 1)["rule_id"]
    assert cams.client.patch(f"/api/rules/{h01}", json={"zone_ids": [zone_id(cams, 1, "waste")]},
                             headers=h).status_code == 422


# ---------------------------------------------------------------- 4. rule ranges, new H-04 values

@pytest.mark.parametrize("camera_id", [1, 2])
def test_new_h04_values_save(cams, camera_id):
    rid = cams.rule("H-04", camera_id)["rule_id"]
    body = {"window_s": 10, "min_ratio": 0.30, "end_gap_s": 60, "cooldown_s": 300}
    r = cams.client.patch(f"/api/rules/{rid}", json=body, headers=cams.headers("tech"))
    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in body} == body
    assert r.headers["X-RADS-Worker-Reload"] == "ok"


@pytest.mark.parametrize("value, ok", [(1, True), (60, True), (120, True), (0.5, False), (121, False)])
def test_end_gap_range_1_to_120(cams, value, ok):
    rid = cams.rule("H-01", 1)["rule_id"]
    r = cams.client.patch(f"/api/rules/{rid}", json={"end_gap_s": value}, headers=cams.headers("admin"))
    assert r.status_code == (200 if ok else 422)


def test_create_rule_with_long_end_gap(cams):
    h = cams.headers("admin")
    waste2 = cams.client.post("/api/zones", json={"camera_id": 2, "name": "Bin 2", "type": "waste",
                                                  "polygon": [[0, 0], [90, 0], [90, 90]]}, headers=h).json()["zone_id"]
    r = cams.client.post("/api/rules", json={"hazard_id": "H-05", "camera_id": 2, "zone_ids": [waste2],
                                             "end_gap_s": 120}, headers=h)
    assert r.status_code == 201 and r.json()["end_gap_s"] == 120


# ---------------------------------------------------------------- 5. deleting a zone incidents refer to

def test_delete_zone_with_incidents_is_409_with_deactivate_hint(cams):
    cams.add_incident("H-05", 1)
    waste = zone_id(cams, 1, "waste")
    r = cams.client.delete(f"/api/zones/{waste}", headers=cams.headers("tech"))
    assert r.status_code == 409 and "deactivate it instead" in r.json()["detail"]
    r = cams.client.patch(f"/api/zones/{waste}", json={"active": False}, headers=cams.headers("tech"))
    assert r.status_code == 200 and r.json()["active"] is False


def test_incident_recorded_between_check_and_delete_is_409_not_500(cams, monkeypatch):
    """The worker may insert an incident after the check; the foreign key then refuses the delete."""
    waste = zone_id(cams, 1, "waste")
    h = cams.headers("tech")
    # Give the H-05 rule a second bin zone, so the foreign key is the only thing that blocks the delete.
    bin2 = cams.client.post("/api/zones", json={"camera_id": 1, "name": "Bin 2", "type": "waste",
                                                "polygon": [[0, 0], [90, 0], [90, 90]]}, headers=h).json()["zone_id"]
    cams.client.patch(f"/api/rules/{cams.rule('H-05', 1)['rule_id']}", json={"zone_ids": [waste, bin2]}, headers=h)
    cams.worker.calls.clear()
    cams.add_incident("H-05", 1)  # zone_id = the rule's first zone, i.e. `waste`
    monkeypatch.setattr(zone_routes, "_has_incidents", lambda db, z: False)  # simulate the race
    r = cams.client.delete(f"/api/zones/{waste}", headers=cams.headers("tech"))
    assert r.status_code == 409 and "deactivate it instead" in r.json()["detail"]
    assert cams.sql("SELECT 1 FROM zones WHERE zone_id = :z", z=waste)  # still there
    assert cams.audit("zone_delete") == [] and cams.worker.reloads == 0


# ---------------------------------------------------------------- 6. fake incidents use rule zones only

def test_fake_incidents_never_use_an_ignore_zone(cams):
    ign = add_ignore(cams)["zone_id"]
    # Even if an ignore zone ends up in rule_zones (written by hand, bypassing the API), it is never used.
    cams.execute("INSERT INTO rule_zones (rule_id, zone_id) VALUES (:r, :z)", r=cams.rule("H-04", 2)["rule_id"], z=ign)
    w = FakeWorker(cams.Session, cams.evidence_dir, None, random.Random(3))
    ids = [w.new_incident() for _ in range(60)]
    rows = cams.sql(f"SELECT i.hazard_id, i.zone_id, i.track_id, i.rule_id, z.type FROM incidents i "
                    f"JOIN zones z USING (zone_id) WHERE i.incident_id IN ({','.join(map(str, ids))})")
    assert all(r["zone_id"] != ign and r["type"] in ("prep", "waste") for r in rows)
    for r in rows:
        assert (r["zone_id"],) in {(x["zone_id"],) for x in cams.sql(
            "SELECT zone_id FROM rule_zones WHERE rule_id = :r", r=r["rule_id"])}
        if r["hazard_id"] in ("H-04", "H-05"):
            assert r["track_id"] is None
        else:
            assert r["track_id"] is not None
    assert {r["hazard_id"] for r in rows} == {"H-01", "H-04", "H-05", "H-06", "H-08"}
