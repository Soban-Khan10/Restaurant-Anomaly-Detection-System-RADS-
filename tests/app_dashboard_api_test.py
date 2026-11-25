"""Phase 6a backend support: /api/incidents/summary, /api/hazards, /api/alerts, CORS for the Vite dev server."""
from __future__ import annotations

from datetime import datetime

import pytest

from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from shared.contract import TZ

VITE = "http://localhost:5173"


# ---------------------------------------------------------------- summary

def test_summary_counts_a_day_range(ctx):
    ctx.add_incident("H-01", 1, "2026-12-01T00:00:00+05:00")
    ctx.add_incident("H-01", 2, "2026-12-01T23:59:59+05:00", status="False alarm")
    ctx.add_incident("H-05", 1, "2026-12-01T12:00:00+05:00", track_id=None, status="Confirmed violation")
    ctx.add_incident("H-08", 2, "2026-12-02T00:00:00+05:00")  # next day
    r = ctx.client.get("/api/incidents/summary?date_from=2026-12-01", headers=ctx.headers("manager"))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["date_from"] == s["date_to"] == "2026-12-01"
    assert s["total"] == 3 and s["unreviewed"] == 1
    assert [(h["hazard_id"], h["count"]) for h in s["by_hazard"]] == [
        ("H-01", 2), ("H-04", 0), ("H-05", 1), ("H-06", 0), ("H-08", 0)]
    assert s["by_hazard"][0]["name"] == "Head cover missing"
    assert s["by_status"] == {"New": 1, "Acknowledged": 0, "Confirmed violation": 1, "False alarm": 1}

    two = ctx.client.get("/api/incidents/summary?date_from=2026-12-01&date_to=2026-12-02",
                         headers=ctx.headers("tech")).json()
    assert two["total"] == 4


def test_summary_defaults_to_today_in_pakistan(ctx):
    ctx.add_incident("H-04", 1, track_id=None)  # now
    s = ctx.client.get("/api/incidents/summary", headers=ctx.headers("tech")).json()
    assert s["date_from"] == datetime.now(TZ).date().isoformat() and s["total"] == 1


def test_summary_validation_and_login(ctx):
    h = ctx.headers("admin")
    assert ctx.client.get("/api/incidents/summary?date_from=2026-12-02&date_to=2026-12-01",
                          headers=h).status_code == 422
    assert ctx.client.get("/api/incidents/summary?date_from=today", headers=h).status_code == 422
    assert ctx.client.get("/api/incidents/summary").status_code == 401


def test_incident_detail_route_still_works_next_to_summary(ctx):
    iid = ctx.add_incident()
    assert ctx.client.get(f"/api/incidents/{iid}", headers=ctx.headers("tech")).json()["incident_id"] == iid


# ---------------------------------------------------------------- hazards + alerts

def test_hazards(ctx):
    r = ctx.client.get("/api/hazards", headers=ctx.headers("manager"))
    assert [(h["hazard_id"], h["tied_to"]) for h in r.json()] == [
        ("H-01", "worker"), ("H-04", "zone"), ("H-05", "zone"), ("H-06", "worker"), ("H-08", "worker")]
    assert ctx.client.get("/api/hazards").status_code == 401


def test_latest_alerts_newest_first_with_incident_details(ctx):
    a = ctx.add_incident("H-01", 1)
    b = ctx.add_incident("H-05", 1, track_id=None, severity="High")
    for iid in (a, b):
        ctx.local.post("/internal/incidents/new", json={"incident_id": iid}, headers=INTERNAL)
    ctx.execute("INSERT INTO alerts (incident_id, channel, kind, sent_at, status) "
                "VALUES (:i, 'whatsapp', 'first', '2030-01-01T00:00:00+05:00', 'failed')", i=a)

    r = ctx.client.get("/api/alerts", headers=ctx.headers("tech"))
    assert r.status_code == 200
    alerts = r.json()
    assert [x["incident_id"] for x in alerts] == [b, a]  # dashboard channel only, newest first
    assert alerts[0] | {"alert_id": 0, "sent_at": "", "start_time": ""} == {
        "alert_id": 0, "incident_id": b, "channel": "dashboard", "kind": "first", "sent_at": "", "status": "sent",
        "hazard_id": "H-05", "hazard_name": "Bin left open", "camera_id": 1, "camera_name": "Camera 1",
        "zone_name": ctx.sql("SELECT name FROM zones WHERE zone_id = :z",
                             z=ctx.rule("H-05", 1)["zone_id"])[0]["name"],
        "severity": "High", "start_time": "", "end_time": None, "incident_status": "New"}

    assert len(ctx.client.get("/api/alerts?limit=1", headers=ctx.headers("tech")).json()) == 1
    wa = ctx.client.get("/api/alerts?channel=whatsapp", headers=ctx.headers("tech")).json()
    assert [(x["incident_id"], x["status"]) for x in wa] == [(a, "failed")]
    assert ctx.client.get("/api/alerts?limit=0", headers=ctx.headers("tech")).status_code == 422
    assert ctx.client.get("/api/alerts").status_code == 401


# ---------------------------------------------------------------- CORS for the Vite dev server

@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_cors_preflight_allows_vite_dev_server(ctx, origin):
    r = ctx.client.options("/api/incidents", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_cors_refuses_other_origins(ctx):
    r = ctx.client.options("/api/incidents", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_exposes_headers_the_dashboard_reads(ctx):
    rid = ctx.rule("H-01")["rule_id"]
    h = {**ctx.headers("tech"), "Origin": VITE}
    r = ctx.client.patch(f"/api/rules/{rid}", json={"window_s": 6}, headers=h)
    assert r.headers["access-control-allow-origin"] == VITE
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "x-rads-worker-reload" in exposed and "content-disposition" in exposed
