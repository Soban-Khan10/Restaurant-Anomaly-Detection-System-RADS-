"""Phase 3: /ws WebSocket hub (incident.new, incident.closed, camera.status) and offline detection."""
from __future__ import annotations

import asyncio
import contextlib

import anyio.from_thread
import pytest
from starlette.websockets import WebSocketDisconnect

from app import ws
from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from shared.contract import Heartbeat, now_iso


@contextlib.contextmanager
def one_loop(client):
    """Run every request and WebSocket of `client` on one event loop, as uvicorn does in production
    (without starting the app lifespan, so no scheduler touches the real database)."""
    with anyio.from_thread.start_blocking_portal() as portal:
        client.portal = portal
        try:
            yield portal
        finally:
            client.portal = None


def _hb(camera_id=1, status="online", fps=4.9):
    return {"camera_id": camera_id, "status": status, "fps": fps, "ts": now_iso()}


def _connect(ctx, username="tech"):
    token = ctx.token(username)
    return ctx.local.websocket_connect(f"/ws?token={token}")


def _initial(sock):
    """The camera.status snapshot sent on connect (one per enabled camera)."""
    return [sock.receive_json(), sock.receive_json()]


# ---------------------------------------------------------------- auth

@pytest.mark.parametrize("query", ["", "?token=", "?token=not-a-jwt"])
def test_ws_rejects_without_valid_token(ctx, query):
    with pytest.raises(WebSocketDisconnect) as e:
        with ctx.local.websocket_connect(f"/ws{query}") as sock:
            sock.receive_json()
    assert e.value.code == 1008


def test_ws_rejects_inactive_user(ctx):
    token = ctx.token("manager")
    ctx.execute("UPDATE users SET active = 0 WHERE username = 'manager'")
    with pytest.raises(WebSocketDisconnect) as e:
        with ctx.local.websocket_connect(f"/ws?token={token}") as sock:
            sock.receive_json()
    assert e.value.code == 1008


# ---------------------------------------------------------------- snapshot on connect

def test_connect_sends_camera_snapshot(ctx):
    with one_loop(ctx.local):
        ctx.local.post("/internal/heartbeat", json=_hb(1), headers=INTERNAL)
        with _connect(ctx) as sock:
            snap = _initial(sock)
    assert [(m["type"], m["camera_id"], m["status"]) for m in snap] == [
        ("camera.status", 1, "online"),
        ("camera.status", 2, "offline"),  # never heard from
    ]
    assert snap[0]["fps"] == 4.9


def test_ping_pong(ctx):
    with one_loop(ctx.local), _connect(ctx) as sock:
        _initial(sock)
        sock.send_text("ping")
        assert sock.receive_text() == "pong"


# ---------------------------------------------------------------- broadcasts

def test_incident_new_and_closed_reach_every_dashboard(ctx):
    iid = ctx.add_incident("H-04", 2, track_id=None)
    with one_loop(ctx.local), _connect(ctx, "tech") as a, _connect(ctx, "manager") as b:
        _initial(a), _initial(b)

        r = ctx.local.post("/internal/incidents/new", json={"incident_id": iid}, headers=INTERNAL)
        assert r.status_code == 200
        for sock in (a, b):
            msg = sock.receive_json()
            assert msg["type"] == "incident.new"
            inc = msg["incident"]
            assert inc["incident_id"] == iid and inc["hazard_id"] == "H-04"
            assert inc["hazard_name"] == "Phone in preparation area" and inc["camera_name"] == "Camera 2"
            assert inc["status"] == "New" and inc["end_time"] is None

        ctx.local.post("/internal/incidents/closed", json={"incident_id": iid}, headers=INTERNAL)
        for sock in (a, b):
            assert sock.receive_json() == {"type": "incident.closed", "incident_id": iid}


def test_heartbeat_forwarded_as_camera_status(ctx):
    with one_loop(ctx.local), _connect(ctx) as sock:
        _initial(sock)
        body = _hb(2, "reconnecting", None)
        ctx.local.post("/internal/heartbeat", json=body, headers=INTERNAL)
        assert sock.receive_json() == {"type": "camera.status", **body}


def test_unknown_incident_broadcasts_nothing(ctx):
    with one_loop(ctx.local), _connect(ctx) as sock:
        _initial(sock)
        assert ctx.local.post("/internal/incidents/new", json={"incident_id": 999},
                              headers=INTERNAL).status_code == 404
        ctx.local.post("/internal/heartbeat", json=_hb(1), headers=INTERNAL)
        assert sock.receive_json()["type"] == "camera.status"  # the next message, not an incident


def test_broadcast_with_no_dashboards_is_fine(ctx):
    iid = ctx.add_incident()
    assert ctx.local.post("/internal/incidents/new", json={"incident_id": iid}, headers=INTERNAL).status_code == 200


def test_closed_dashboard_is_dropped(ctx):
    with one_loop(ctx.local):
        with _connect(ctx) as sock:
            _initial(sock)
        ctx.local.post("/internal/heartbeat", json=_hb(1), headers=INTERNAL)
        assert ws.hub.connections == set()


# ---------------------------------------------------------------- hub + offline detection (unit)

class FakeSocket:
    def __init__(self, broken=False):
        self.sent, self.broken = [], broken

    async def send_json(self, msg):
        if self.broken:
            raise RuntimeError("socket closed")
        self.sent.append(msg)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_hub_drops_broken_connections():
    hub = ws.Hub()
    good, bad = FakeSocket(), FakeSocket(broken=True)
    hub.add(good), hub.add(bad)
    assert asyncio.run(hub.broadcast({"type": "x"})) == 1
    assert good.sent == [{"type": "x"}] and hub.connections == {good}


def test_offline_after_15s_without_heartbeat():
    clock = Clock()
    mon, hub, sock = ws.CameraMonitor(clock=clock), ws.Hub(), FakeSocket()
    hub.add(sock)
    mon.record(Heartbeat(**_hb(1)))
    mon.record(Heartbeat(**_hb(2)))

    clock.t += 14
    assert asyncio.run(ws.check_offline(hub, mon)) == 0
    mon.record(Heartbeat(**_hb(2)))  # camera 2 keeps beating

    clock.t += 2  # camera 1: 16 s of silence
    assert asyncio.run(ws.check_offline(hub, mon)) == 1
    assert [(m["camera_id"], m["status"], m["fps"]) for m in sock.sent] == [(1, "offline", None)]
    assert mon.status_of(1).status == "offline" and mon.status_of(2).status == "online"

    assert asyncio.run(ws.check_offline(hub, mon)) == 0  # reported once, not every second
    mon.record(Heartbeat(**_hb(1)))  # back online
    assert mon.status_of(1).status == "online"
    clock.t += 16
    assert asyncio.run(ws.check_offline(hub, mon)) == 2  # both silent now


def test_offline_reaches_a_real_dashboard(ctx, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ws.cameras, "clock", clock)
    with one_loop(ctx.local) as portal, _connect(ctx) as sock:
        _initial(sock)
        ctx.local.post("/internal/heartbeat", json=_hb(1), headers=INTERNAL)
        assert sock.receive_json()["status"] == "online"
        clock.t += 16
        assert portal.call(ws.check_offline) == 1
        msg = sock.receive_json()
        assert msg["type"] == "camera.status" and msg["camera_id"] == 1 and msg["status"] == "offline"
