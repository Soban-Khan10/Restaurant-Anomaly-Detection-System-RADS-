"""Phase 2: scripts/fake_incidents.py against a temporary DB, calling the app through the TestClient."""
from __future__ import annotations

import random

import httpx
from PIL import Image

from app_helpers import INTERNAL, ctx  # noqa: F401  (ctx is a fixture)
from scripts.fake_incidents import MODEL_VERSION, FakeWorker, Notifier, purge, run
from shared.contract import INTERNAL_HEADER


class SpyNotifier(Notifier):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls: list[tuple[str, bool]] = []

    def post(self, path, body, retries=3):
        ok = super().post(path, body, retries)
        self.calls.append((path, ok))
        return ok


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _worker(ctx, notifier=None, **kw):
    notifier = notifier or SpyNotifier(ctx.local, INTERNAL[INTERNAL_HEADER], retry_delay=0)
    return FakeWorker(ctx.Session, ctx.evidence_dir, notifier, random.Random(1), **kw)


def test_one_cycle_end_to_end(ctx):
    clock = FakeClock()
    w = _worker(ctx, min_duration=10, max_duration=10, clock=clock)

    iid = w.new_incident()
    inc = ctx.sql("SELECT * FROM incidents WHERE incident_id = :i", i=iid)[0]
    assert inc["model_version"] == MODEL_VERSION and inc["end_time"] is None and inc["status"] == "New"
    zone_type = ctx.sql("SELECT type FROM zones WHERE zone_id = :z", z=inc["zone_id"])[0]["type"]
    assert zone_type == ("waste" if inc["hazard_id"] == "H-05" else "prep")
    if inc["hazard_id"] in ("H-04", "H-05"):
        assert inc["track_id"] is None
    else:
        assert inc["track_id"] is not None

    ev = ctx.sql("SELECT * FROM evidence WHERE incident_id = :i", i=iid)[0]
    day = inc["start_time"][:10]
    assert ev["file_path"] == f"{day}/cam{inc['camera_id']}/{iid}.jpg"
    with Image.open(ctx.evidence_dir / ev["file_path"]) as img:
        assert img.format == "JPEG"

    clock.t = 5
    w.close_due()
    assert ctx.sql("SELECT end_time FROM incidents WHERE incident_id = :i", i=iid)[0]["end_time"] is None
    clock.t = 11
    w.close_due()
    closed = ctx.sql("SELECT * FROM incidents WHERE incident_id = :i", i=iid)[0]
    assert closed["end_time"] is not None and closed["conf_max"] >= closed["conf_avg"]

    w.heartbeats()
    health = ctx.sql("SELECT * FROM camera_health ORDER BY camera_id")
    assert [h["camera_id"] for h in health] == [1, 2] and all(h["status"] == "online" for h in health)

    assert w.notifier.calls == [("/internal/incidents/new", True), ("/internal/incidents/closed", True),
                                ("/internal/heartbeat", True), ("/internal/heartbeat", True)]

    # The app shows it, and serves its image.
    h = ctx.headers("manager")
    items = ctx.client.get("/api/incidents", headers=h).json()["items"]
    assert [i["incident_id"] for i in items] == [iid]
    r = ctx.client.get(f"/api/evidence/{items[0]['evidence_id']}/image", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"


def test_offline_camera_sends_no_heartbeat(ctx):
    w = _worker(ctx, offline_camera=2)
    w.heartbeats()
    assert [h["camera_id"] for h in ctx.sql("SELECT camera_id FROM camera_health")] == [1]


def test_only_worker_columns_written(ctx):
    w = _worker(ctx, min_duration=0, max_duration=0, clock=FakeClock())
    iid = w.new_incident()
    w.close_due()
    inc = ctx.sql("SELECT * FROM incidents WHERE incident_id = :i", i=iid)[0]
    assert inc["reviewed_by"] is None and inc["reviewed_at"] is None and inc["escalated_at"] is None
    assert ctx.sql("SELECT COUNT(*) AS n FROM alerts")[0]["n"] == 0


def test_run_loop_with_count(ctx):
    w = _worker(ctx, min_duration=0, max_duration=0)
    run(w, interval=0, count=3, tick=0)
    rows = ctx.sql("SELECT end_time FROM incidents")
    assert len(rows) == 3 and all(r["end_time"] for r in rows)
    assert not w.open


def test_api_down_retries_three_times_and_never_raises(ctx):
    attempts = []

    def down(request):
        attempts.append(request.url.path)
        raise httpx.ConnectError("connection refused")

    n = Notifier(httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(down)),
                 "tok", retry_delay=0)
    w = _worker(ctx, notifier=n)
    iid = w.new_incident()
    assert attempts == ["/internal/incidents/new"] * 3
    assert ctx.sql("SELECT 1 FROM incidents WHERE incident_id = :i", i=iid)  # row is still there


def test_rejected_call_is_not_retried(ctx):
    attempts = []

    def unauthorized(request):
        attempts.append(1)
        return httpx.Response(401, json={"detail": "bad token"})

    n = Notifier(httpx.Client(base_url="http://x", transport=httpx.MockTransport(unauthorized)), "t", retry_delay=0)
    assert n.post("/internal/incidents/new", {"incident_id": 1}) is False
    assert len(attempts) == 1


def test_purge_removes_only_fake_rows_and_files(ctx):
    real = ctx.add_incident()
    real_ev = ctx.add_evidence(real)
    w = _worker(ctx, notifier=None, min_duration=0, max_duration=0, clock=FakeClock())
    fake_ids = [w.new_incident() for _ in range(3)]
    files = [ctx.evidence_dir / r["file_path"] for r in ctx.sql(
        "SELECT file_path FROM evidence WHERE incident_id IN (%s)" % ",".join(map(str, fake_ids)))]
    assert all(f.exists() for f in files)

    assert purge(ctx.Session, ctx.evidence_dir) == 3
    assert [r["incident_id"] for r in ctx.sql("SELECT incident_id FROM incidents")] == [real]
    assert [r["evidence_id"] for r in ctx.sql("SELECT evidence_id FROM evidence")] == [real_ev]
    assert not any(f.exists() for f in files)
    assert (ctx.evidence_dir / f"2026-12-02/cam1/{real}.jpg").exists()
    assert purge(ctx.Session, ctx.evidence_dir) == 0
