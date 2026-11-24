"""Phase 4: GET /api/cameras/{id}/stream and /snapshot (relay of the worker's MJPEG), plus the fake worker
stand-in in scripts/fake_incidents.py (/reload, /stream)."""
from __future__ import annotations

import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.worker import WorkerClient, extract_jpeg, get_worker
from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from scripts.fake_incidents import create_worker_app, render_frame
from shared.contract import INTERNAL_HEADER

JPEG_A = b"\xff\xd8" + b"A" * 50 + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"B" * 70 + b"\xff\xd9"


def part(jpeg, length=True):
    head = b"--frame\r\nContent-Type: image/jpeg\r\n"
    if length:
        head += b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n"
    return head + b"\r\n" + jpeg + b"\r\n"


def mjpeg(request, frames=(JPEG_A, JPEG_B), length=True):
    if request.url.path != "/stream/1":
        return httpx.Response(404)
    return httpx.Response(200, content=b"".join(part(f, length) for f in frames),
                          headers={"content-type": "multipart/x-mixed-replace; boundary=frame"})


# ---------------------------------------------------------------- stream relay

def test_stream_relays_bytes_with_internal_token(ctx):
    ctx.worker.handler = mjpeg
    r = ctx.client.get("/api/cameras/1/stream", headers=ctx.headers("manager"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert r.content == part(JPEG_A) + part(JPEG_B)
    assert ctx.worker.calls == [("GET", "/stream/1", INTERNAL[INTERNAL_HEADER])]


def test_stream_accepts_token_in_query_for_img_tags(ctx):
    ctx.worker.handler = mjpeg
    token = ctx.token("tech")
    assert ctx.client.get(f"/api/cameras/1/stream?token={token}").status_code == 200
    assert ctx.client.get(f"/api/cameras/1/snapshot?token={token}").content == JPEG_A


@pytest.mark.parametrize("url", ["/api/cameras/1/stream", "/api/cameras/1/snapshot",
                                 "/api/cameras/1/stream?token=bad", "/api/cameras/1/snapshot?token="])
def test_live_view_requires_login(ctx, url):
    assert ctx.client.get(url).status_code == 401
    assert ctx.worker.calls == []


def test_worker_down_is_503(ctx):
    ctx.worker.down = True
    h = ctx.headers("tech")
    for url in ("/api/cameras/1/stream", "/api/cameras/1/snapshot"):
        r = ctx.client.get(url, headers=h)
        assert r.status_code == 503 and "worker is not running" in r.json()["detail"]


class BreaksAfterOneFrame(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield part(JPEG_A)
        raise httpx.ReadError("worker went away")


def test_worker_dying_mid_stream_ends_stream_cleanly(ctx, caplog):
    ctx.worker.handler = lambda req: httpx.Response(
        200, stream=BreaksAfterOneFrame(), headers={"content-type": "multipart/x-mixed-replace; boundary=frame"})
    r = ctx.client.get("/api/cameras/1/stream", headers=ctx.headers("tech"))
    assert r.status_code == 200 and r.content == part(JPEG_A)
    assert any("worker stream broke" in m for m in caplog.messages)


def test_worker_error_status(ctx):
    ctx.worker.handler = lambda req: httpx.Response(500)
    assert ctx.client.get("/api/cameras/1/stream", headers=ctx.headers("tech")).status_code == 503
    ctx.worker.handler = mjpeg  # answers 404 for camera 2
    assert ctx.client.get("/api/cameras/2/stream", headers=ctx.headers("tech")).status_code == 404


def test_unknown_camera_is_404_without_calling_worker(ctx):
    h = ctx.headers("tech")
    assert ctx.client.get("/api/cameras/99/stream", headers=h).status_code == 404
    assert ctx.client.get("/api/cameras/99/snapshot", headers=h).status_code == 404
    assert ctx.worker.calls == []


# ---------------------------------------------------------------- snapshot

@pytest.mark.parametrize("length", [True, False])
def test_snapshot_returns_first_frame(ctx, length):
    ctx.worker.handler = lambda req: mjpeg(req, length=length)
    r = ctx.client.get("/api/cameras/1/snapshot", headers=ctx.headers("admin"))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG_A


def test_snapshot_without_a_frame_is_503(ctx):
    ctx.worker.handler = lambda req: httpx.Response(200, content=b"--frame\r\n\r\nnot a jpeg")
    assert ctx.client.get("/api/cameras/1/snapshot", headers=ctx.headers("admin")).status_code == 503


def test_extract_jpeg():
    assert extract_jpeg(part(JPEG_A)[:20]) is None
    assert extract_jpeg(part(JPEG_A)) == JPEG_A
    assert extract_jpeg(part(JPEG_A, length=False)) == JPEG_A
    # Content-Length wins over a stray end marker inside the image data.
    tricky = b"\xff\xd8" + b"x\xff\xd9y" + b"\xff\xd9"
    assert extract_jpeg(part(tricky)) == tricky
    assert extract_jpeg(part(tricky)[:-10]) is None  # incomplete


# ---------------------------------------------------------------- end to end with the fake worker stand-in

def test_relay_through_the_fake_worker(ctx):
    fake = create_worker_app(ctx.Session, INTERNAL[INTERNAL_HEADER], offline_camera=2, max_frames=2, fps=100)
    app.dependency_overrides[get_worker] = lambda: WorkerClient(
        "http://127.0.0.1:8001", INTERNAL[INTERNAL_HEADER], transport=httpx.ASGITransport(app=fake))
    h = ctx.headers("tech")

    r = ctx.client.get("/api/cameras/1/stream", headers=h)
    assert r.status_code == 200 and r.content.count(b"--frame\r\n") == 2

    snap = ctx.client.get("/api/cameras/1/snapshot", headers=h)
    assert snap.status_code == 200
    with Image.open(io.BytesIO(snap.content)) as img:
        assert img.format == "JPEG" and img.size == (960, 540)

    assert ctx.client.get("/api/cameras/2/stream", headers=h).status_code == 503  # offline camera


# ---------------------------------------------------------------- the stand-in itself

def test_fake_worker_endpoints(ctx):
    token = INTERNAL[INTERNAL_HEADER]
    fake = create_worker_app(ctx.Session, token, max_frames=3, fps=100)
    c = TestClient(fake)

    assert c.post("/reload", json={}).status_code == 401
    assert c.post("/reload", json={}, headers={INTERNAL_HEADER: "wrong"}).status_code == 401
    r = c.post("/reload", json={}, headers={INTERNAL_HEADER: token})
    assert r.status_code == 200 and r.json() == {"ok": True} and fake.state.reloads == 1

    assert c.get("/stream/1").status_code == 401
    r = c.get("/stream/1", headers={INTERNAL_HEADER: token})
    assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert r.content.count(b"Content-Type: image/jpeg") == 3

    ctx.execute("UPDATE cameras SET enabled = 0 WHERE camera_id = 2")
    assert c.get("/stream/2", headers={INTERNAL_HEADER: token}).status_code == 404
    assert c.get("/stream/9", headers={INTERNAL_HEADER: token}).status_code == 404


def test_app_save_reloads_the_fake_worker(ctx):
    """The real app -> fake worker path for message 7 (sync client, so via the fake's TestClient transport)."""
    fake = create_worker_app(ctx.Session, INTERNAL[INTERNAL_HEADER])
    fake_client = TestClient(fake)
    app.dependency_overrides[get_worker] = lambda: WorkerClient(
        "http://testserver", INTERNAL[INTERNAL_HEADER], transport=fake_client._transport)
    rid = ctx.rule("H-01")["rule_id"]
    r = ctx.client.patch(f"/api/rules/{rid}", json={"window_s": 7}, headers=ctx.headers("tech"))
    assert r.headers["X-RADS-Worker-Reload"] == "ok" and fake.state.reloads == 1


def test_render_frame_draws_zones_at_camera_scale():
    cam = {"name": "Camera 1", "frame_width": 1280, "frame_height": 720}
    zones = [{"name": "Prep", "type": "prep", "active": 1, "polygon": "[[100,100],[1180,100],[1180,620],[100,620]]"}]
    with Image.open(io.BytesIO(render_frame(cam, zones, 5))) as img:
        assert img.size == (960, 540)
        r, g, b = img.getpixel((480, 465))  # bottom edge of the zone: y 620 * 0.75
        assert g > 150 and g > r and g > b  # drawn in the prep colour (green)
