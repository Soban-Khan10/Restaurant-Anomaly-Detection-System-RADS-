"""Phase 2: GET /api/evidence/{evidence_id}/image."""
from __future__ import annotations

import pytest

from app_helpers import ctx  # noqa: F401  (fixture)

JPEG = b"\xff\xd8\xff\xe0" + b"pixels" * 100 + b"\xff\xd9"


@pytest.mark.parametrize("viewer", ["admin", "manager", "tech"])
def test_image_served_and_every_view_audited(ctx, viewer):
    iid = ctx.add_incident()
    eid = ctx.add_evidence(iid, content=JPEG)
    h = ctx.headers(viewer)
    for _ in range(2):
        r = ctx.client.get(f"/api/evidence/{eid}/image", headers=h)
        assert r.status_code == 200
        assert r.content == JPEG
        assert r.headers["content-type"] == "image/jpeg"
        assert "no-store" in r.headers["cache-control"]
    audit = ctx.audit("view_evidence")
    assert len(audit) == 2
    assert all(a["user_id"] == ctx.ids[viewer] and a["target_type"] == "evidence" and a["target_id"] == eid
               and a["details"] == {"incident_id": iid} for a in audit)


def test_requires_login(ctx):
    eid = ctx.add_evidence(ctx.add_incident())
    assert ctx.client.get(f"/api/evidence/{eid}/image").status_code == 401
    assert ctx.audit("view_evidence") == []


def test_deleted_evidence_is_410(ctx):
    eid = ctx.add_evidence(ctx.add_incident(), write_file=False, deleted_at="2026-12-30T03:00:00+05:00")
    assert ctx.client.get(f"/api/evidence/{eid}/image", headers=ctx.headers("admin")).status_code == 410
    assert ctx.audit("view_evidence") == []


def test_unknown_evidence_is_404(ctx):
    assert ctx.client.get("/api/evidence/999/image", headers=ctx.headers("admin")).status_code == 404


def test_missing_file_is_404(ctx):
    eid = ctx.add_evidence(ctx.add_incident(), write_file=False)
    assert ctx.client.get(f"/api/evidence/{eid}/image", headers=ctx.headers("admin")).status_code == 404
    assert ctx.audit("view_evidence") == []


@pytest.mark.parametrize("bad_path", ["../rads.db", "../../secret.jpg", "2026-12-02/../../outside.jpg"])
def test_path_outside_evidence_dir_is_404(ctx, bad_path):
    outside = ctx.evidence_dir.parent / "outside.jpg"
    outside.write_bytes(JPEG)
    eid = ctx.add_evidence(ctx.add_incident(), bad_path, write_file=False)
    assert ctx.client.get(f"/api/evidence/{eid}/image", headers=ctx.headers("admin")).status_code == 404
