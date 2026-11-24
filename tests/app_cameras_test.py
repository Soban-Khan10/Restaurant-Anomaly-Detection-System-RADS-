"""Phase 4: /api/cameras CRUD, rtsp_url visibility, audit old/new, worker reload after save."""
from __future__ import annotations

import logging

import pytest

from app.routes.cameras import mask_rtsp
from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from shared.contract import INTERNAL_HEADER, now_iso

SECRET = "secret-cam-pass"  # the password inside the seeded CAM1_RTSP / CAM2_RTSP test URLs
NEW_CAM = {"name": "Camera 3", "rtsp_url": "rtsp://admin:n3w-pass@192.168.1.66:554/Streaming/Channels/101",
           "target_fps": 5, "frame_width": 1920, "frame_height": 1080}


# ---------------------------------------------------------------- read + rtsp_url visibility

@pytest.mark.parametrize("username", ["admin", "tech"])
def test_settings_roles_see_rtsp_url(ctx, username):
    h = ctx.headers(username)
    cams = ctx.client.get("/api/cameras", headers=h).json()
    assert [c["camera_id"] for c in cams] == [1, 2]
    assert cams[0]["rtsp_url"].startswith("rtsp://user:")
    assert ctx.client.get("/api/cameras/1", headers=h).json()["rtsp_url"] == cams[0]["rtsp_url"]


def test_manager_never_gets_rtsp_url(ctx):
    h = ctx.headers("manager")
    for r in (ctx.client.get("/api/cameras", headers=h), ctx.client.get("/api/cameras/2", headers=h)):
        assert r.status_code == 200
        assert "rtsp_url" not in r.text and SECRET not in r.text and "rtsp://" not in r.text
    assert ctx.client.get("/api/cameras", headers=h).json()[0]["name"] == "Camera 1"


def test_camera_list_shows_live_status(ctx):
    ctx.local.post("/internal/heartbeat", json={"camera_id": 1, "status": "online", "fps": 4.9, "ts": now_iso()},
                   headers=INTERNAL)
    cams = ctx.client.get("/api/cameras", headers=ctx.headers("manager")).json()
    assert [(c["status"], c["fps"]) for c in cams] == [("online", 4.9), ("offline", None)]


def test_read_requires_login_and_404(ctx):
    assert ctx.client.get("/api/cameras").status_code == 401
    assert ctx.client.get("/api/cameras/99", headers=ctx.headers("tech")).status_code == 404


# ---------------------------------------------------------------- create

def test_technical_creates_camera_audit_and_reload(ctx):
    r = ctx.client.post("/api/cameras", json=NEW_CAM, headers=ctx.headers("tech"))
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["camera_id"] == 3 and cam["rtsp_url"] == NEW_CAM["rtsp_url"] and cam["enabled"] is True
    assert r.headers["X-RADS-Worker-Reload"] == "ok"
    assert ctx.worker.calls == [("POST", "/reload", INTERNAL[INTERNAL_HEADER])]

    audit = ctx.audit("camera_create")
    assert len(audit) == 1 and audit[0]["user_id"] == ctx.ids["tech"] and audit[0]["target_id"] == 3
    assert audit[0]["details"]["new"]["rtsp_url"] == "rtsp://admin:***@192.168.1.66:554/Streaming/Channels/101"
    assert "n3w-pass" not in str(audit[0])


def test_manager_cannot_change_cameras(ctx):
    h = ctx.headers("manager")
    assert ctx.client.post("/api/cameras", json=NEW_CAM, headers=h).status_code == 403
    assert ctx.client.patch("/api/cameras/1", json={"name": "x"}, headers=h).status_code == 403
    assert ctx.client.delete("/api/cameras/2", headers=h).status_code == 403
    assert ctx.worker.calls == []


@pytest.mark.parametrize("bad", [
    {"rtsp_url": "192.168.1.66"},
    {"rtsp_url": "ftp://cam/1"},
    {"target_fps": 0},
    {"target_fps": 31},
    {"frame_width": 1920, "frame_height": None},
    {"frame_width": 100, "frame_height": 100},
    {"name": ""},
])
def test_create_validation(ctx, bad):
    body = {**NEW_CAM, **bad}
    if bad.get("frame_height", 0) is None:
        body.pop("frame_height")
    assert ctx.client.post("/api/cameras", json=body, headers=ctx.headers("admin")).status_code == 422
    assert ctx.worker.calls == []


# ---------------------------------------------------------------- update

def test_update_audit_masks_password_and_stores_old_new(ctx):
    r = ctx.client.patch("/api/cameras/1", json={"name": "Kitchen left", "target_fps": 4,
                                                 "rtsp_url": "rtsp://user:other-pass@192.0.2.9:554/1"},
                         headers=ctx.headers("admin"))
    assert r.status_code == 200 and r.json()["name"] == "Kitchen left"
    details = ctx.audit("camera_update")[0]["details"]
    assert details == {
        "name": {"old": "Camera 1", "new": "Kitchen left"},
        "target_fps": {"old": 5.0, "new": 4.0},
        "rtsp_url": {"old": "rtsp://user:***@192.0.2.1:554/1", "new": "rtsp://user:***@192.0.2.9:554/1"},
    }
    assert SECRET not in str(details) and "other-pass" not in str(details)
    assert ctx.worker.reloads == 1


def test_frame_size_must_fit_existing_zones(ctx):
    h = ctx.headers("tech")
    # The seeded zones are drawn for 1920x1080.
    r = ctx.client.patch("/api/cameras/1", json={"frame_width": 1280, "frame_height": 720}, headers=h)
    assert r.status_code == 409 and "outside 1280x720" in r.json()["detail"]
    assert ctx.client.patch("/api/cameras/1", json={"frame_width": 1920, "frame_height": 1080},
                            headers=h).status_code == 200
    assert ctx.client.patch("/api/cameras/1", json={"frame_width": 1920}, headers=h).status_code == 422


def test_update_nulls_rejected(ctx):
    assert ctx.client.patch("/api/cameras/1", json={"name": None}, headers=ctx.headers("tech")).status_code == 422


# ---------------------------------------------------------------- delete

def test_delete_new_camera(ctx):
    h = ctx.headers("admin")
    cam_id = ctx.client.post("/api/cameras", json=NEW_CAM, headers=h).json()["camera_id"]
    assert ctx.client.delete(f"/api/cameras/{cam_id}", headers=h).status_code == 204
    assert ctx.client.get(f"/api/cameras/{cam_id}", headers=h).status_code == 404
    assert ctx.audit("camera_delete")[0]["details"]["old"]["name"] == "Camera 3"
    assert ctx.worker.reloads == 2


def test_delete_camera_without_history_takes_its_rules_and_zones(ctx):
    r = ctx.client.delete("/api/cameras/2", headers=ctx.headers("tech"))
    assert r.status_code == 204
    assert ctx.sql("SELECT * FROM rules WHERE camera_id = 2") == []
    assert ctx.sql("SELECT * FROM zones WHERE camera_id = 2") == []
    details = ctx.audit("camera_delete")[0]["details"]
    assert len(details["deleted_rules"]) == 4 and len(details["deleted_zones"]) == 1


def test_delete_camera_with_history_refused(ctx):
    ctx.add_incident(camera_id=1)
    r = ctx.client.delete("/api/cameras/1", headers=ctx.headers("admin"))
    assert r.status_code == 409 and "disable it instead" in r.json()["detail"]
    ctx.execute("INSERT INTO camera_health (camera_id, status, last_seen) VALUES (2, 'online', :t)", t=now_iso())
    assert ctx.client.delete("/api/cameras/2", headers=ctx.headers("admin")).status_code == 409
    assert ctx.worker.calls == []


# ---------------------------------------------------------------- worker down

def test_worker_down_still_saves_and_warns(ctx, caplog):
    ctx.worker.down = True
    with caplog.at_level(logging.WARNING, logger="rads.app.worker"):
        r = ctx.client.patch("/api/cameras/1", json={"enabled": False}, headers=ctx.headers("tech"))
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert r.headers["X-RADS-Worker-Reload"] == "failed"
    assert ctx.sql("SELECT enabled FROM cameras WHERE camera_id = 1")[0]["enabled"] == 0
    assert any("worker /reload failed" in m for m in caplog.messages)


def test_mask_rtsp():
    assert mask_rtsp("rtsp://u:p@h:554/x?a=1") == "rtsp://u:***@h:554/x?a=1"
    assert mask_rtsp("rtsp://h/x") == "rtsp://h/x"
    assert mask_rtsp(None) is None
