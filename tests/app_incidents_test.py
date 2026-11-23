"""Phase 2: GET /api/incidents (filters, paging), GET /api/incidents/{id}, PATCH /api/incidents/{id}/review."""
from __future__ import annotations

import pytest

from app_helpers import ctx  # noqa: F401  (fixture)

T1 = "2026-12-01T09:00:00+05:00"
T2 = "2026-12-01T23:59:59+05:00"
T3 = "2026-12-02T08:30:00+05:00"
T4 = "2026-12-03T12:00:00+05:00"


@pytest.fixture
def four(ctx):
    """Four incidents on three days, mixed camera / hazard / status / severity."""
    ids = {
        "a": ctx.add_incident("H-01", 1, T1),
        "b": ctx.add_incident("H-05", 1, T2, track_id=None, status="Acknowledged"),
        "c": ctx.add_incident("H-04", 2, T3, track_id=None, severity="High"),
        "d": ctx.add_incident("H-08", 2, T4, status="False alarm"),
    }
    return ids


def _ids(r):
    assert r.status_code == 200, r.text
    return [i["incident_id"] for i in r.json()["items"]]


# ---------------------------------------------------------------- list

def test_list_newest_first_with_names(ctx, four):
    r = ctx.client.get("/api/incidents", headers=ctx.headers("tech"))
    assert _ids(r) == [four["d"], four["c"], four["b"], four["a"]]
    body = r.json()
    assert body["total"] == 4 and body["page"] == 1
    first = body["items"][0]
    assert first["hazard_name"] == "Mask missing" and first["camera_name"] == "Camera 2"
    assert first["zone_name"] == "Prep area 2"
    assert first["evidence_id"] is None


def test_list_pagination(ctx, four):
    h = ctx.headers("manager")
    p1 = ctx.client.get("/api/incidents?page_size=3&page=1", headers=h)
    p2 = ctx.client.get("/api/incidents?page_size=3&page=2", headers=h)
    assert _ids(p1) == [four["d"], four["c"], four["b"]] and p1.json()["total"] == 4
    assert _ids(p2) == [four["a"]] and p2.json()["total"] == 4
    assert ctx.client.get("/api/incidents?page=0", headers=h).status_code == 422
    assert ctx.client.get("/api/incidents?page_size=201", headers=h).status_code == 422


@pytest.mark.parametrize("query, expected", [
    ("camera_id=1", ["b", "a"]),
    ("hazard_id=H-04", ["c"]),
    ("severity=High", ["c"]),
    ("status=New", ["c", "a"]),
    ("status=New&status=Acknowledged", ["c", "b", "a"]),
    ("status=False%20alarm", ["d"]),
    ("date_from=2026-12-02", ["d", "c"]),
    ("date_to=2026-12-01", ["b", "a"]),                      # date-only end includes 23:59:59
    ("date_from=2026-12-01&date_to=2026-12-02", ["c", "b", "a"]),
    ("date_from=2026-12-01T10:00:00&date_to=2026-12-02T08:30:00", ["c", "b"]),  # naive = +05:00
    ("date_from=2026-12-02T03:00:00Z", ["d", "c"]),           # 03:00Z = 08:00+05:00
    ("camera_id=2&status=New", ["c"]),
    ("camera_id=99", []),
])
def test_list_filters(ctx, four, query, expected):
    r = ctx.client.get(f"/api/incidents?{query}", headers=ctx.headers("admin"))
    assert _ids(r) == [four[k] for k in expected]
    assert r.json()["total"] == len(expected)


@pytest.mark.parametrize("query", ["hazard_id=H-99", "severity=Huge", "status=Done", "date_from=yesterday",
                                   "camera_id=one"])
def test_list_bad_filters(ctx, query):
    assert ctx.client.get(f"/api/incidents?{query}", headers=ctx.headers("admin")).status_code == 422


def test_list_requires_login(ctx):
    assert ctx.client.get("/api/incidents").status_code == 401


def test_list_shows_first_evidence_id(ctx):
    iid = ctx.add_incident()
    e1 = ctx.add_evidence(iid)
    ctx.add_evidence(iid, f"2026-12-02/cam1/{iid}_2.jpg")
    r = ctx.client.get("/api/incidents", headers=ctx.headers("tech"))
    assert r.json()["items"][0]["evidence_id"] == e1


# ---------------------------------------------------------------- detail

def test_detail_contents(ctx):
    iid = ctx.add_incident("H-04", 1, T3, track_id=None)
    eid = ctx.add_evidence(iid)
    ctx.execute("INSERT INTO alerts (incident_id, channel, kind, sent_at, status) "
                "VALUES (:i, 'dashboard', 'first', :t, 'sent')", i=iid, t=T3)

    r = ctx.client.get(f"/api/incidents/{iid}", headers=ctx.headers("tech"))
    assert r.status_code == 200
    d = r.json()
    assert d["incident_id"] == iid and d["track_id"] is None
    assert d["hazard"] == {"hazard_id": "H-04", "name": "Phone in preparation area", "violation_class": "phone",
                           "compliant_class": None, "tied_to": "zone"}
    assert d["rule"]["min_ratio"] == 0.30 and d["rule"]["conf_threshold"] == 0.45
    assert d["camera"]["name"] == "Camera 1"
    assert "rtsp_url" not in d["camera"] and "secret-cam-pass" not in r.text
    assert d["zone"]["type"] == "prep" and isinstance(d["zone"]["polygon"], list)
    assert d["evidence"] == [{
        "evidence_id": eid, "kind": "snapshot", "file_path": f"2026-12-02/cam1/{iid}.jpg",
        "captured_at": d["evidence"][0]["captured_at"], "detections": [{"class": "head_uncovered"}],
        "deleted": False, "deleted_at": None, "image_url": f"/api/evidence/{eid}/image"}]
    assert len(d["alerts"]) == 1 and d["alerts"][0]["channel"] == "dashboard"
    assert d["reviewed_by_user"] is None


def test_detail_404_and_login(ctx):
    assert ctx.client.get("/api/incidents/999", headers=ctx.headers("admin")).status_code == 404
    assert ctx.client.get("/api/incidents/1").status_code == 401


# ---------------------------------------------------------------- review

@pytest.mark.parametrize("reviewer", ["manager", "admin"])
def test_review_sets_review_columns_and_audits(ctx, reviewer):
    iid = ctx.add_incident(end_time=None)
    before = ctx.sql("SELECT * FROM incidents WHERE incident_id = :i", i=iid)[0]

    r = ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "Confirmed violation", "note": "seen on CCTV"},
                         headers=ctx.headers(reviewer))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "Confirmed violation" and d["review_note"] == "seen on CCTV"
    assert d["reviewed_by"] == ctx.ids[reviewer] and d["reviewed_at"].endswith("+05:00")
    assert d["reviewed_by_user"]["username"] == reviewer

    after = ctx.sql("SELECT * FROM incidents WHERE incident_id = :i", i=iid)[0]
    review_cols = {"status", "reviewed_by", "reviewed_at", "review_note"}
    assert {k: v for k, v in after.items() if k not in review_cols} == \
           {k: v for k, v in before.items() if k not in review_cols}  # worker columns untouched

    audit = ctx.audit("incident_review")
    assert len(audit) == 1
    assert audit[0]["user_id"] == ctx.ids[reviewer] and audit[0]["target_id"] == iid
    assert audit[0]["details"] == {"status": {"old": "New", "new": "Confirmed violation"}, "note": "seen on CCTV"}


def test_re_review_keeps_note_when_omitted(ctx):
    iid = ctx.add_incident()
    h = ctx.headers("manager")
    ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "Acknowledged", "note": "checking"}, headers=h)
    r = ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "False alarm"}, headers=h)
    assert r.json()["status"] == "False alarm" and r.json()["review_note"] == "checking"
    assert ctx.audit("incident_review")[-1]["details"]["status"] == {"old": "Acknowledged", "new": "False alarm"}


def test_technical_cannot_review(ctx):
    iid = ctx.add_incident()
    r = ctx.client.patch(f"/api/incidents/{iid}/review", json={"status": "Acknowledged"},
                         headers=ctx.headers("tech"))
    assert r.status_code == 403
    assert ctx.sql("SELECT status FROM incidents WHERE incident_id = :i", i=iid)[0]["status"] == "New"
    assert ctx.audit("incident_review") == []


@pytest.mark.parametrize("body", [{"status": "New"}, {"status": "Closed"}, {}, {"status": "Acknowledged",
                                                                              "note": "x" * 1001}])
def test_review_validation(ctx, body):
    iid = ctx.add_incident()
    assert ctx.client.patch(f"/api/incidents/{iid}/review", json=body,
                            headers=ctx.headers("admin")).status_code == 422


def test_review_404_and_login(ctx):
    assert ctx.client.patch("/api/incidents/999/review", json={"status": "Acknowledged"},
                            headers=ctx.headers("admin")).status_code == 404
    assert ctx.client.patch("/api/incidents/1/review", json={"status": "Acknowledged"}).status_code == 401
