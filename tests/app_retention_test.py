"""Phase 5: evidence retention (fake old day folders), DB backups, the daily job, the manual trigger, scheduling."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app import retention
from app.main import add_jobs
from app_helpers import ctx  # noqa: F401  (ctx is a fixture)
from shared.config import get_settings
from shared.contract import TZ

TODAY = date(2026, 11, 24)  # cutoff = 2026-10-25: folders dated before it are older than 30 days


@pytest.fixture
def old_evidence(ctx):
    """Day folders around the 30-day cutoff, each with one incident + evidence file, plus things to leave alone."""
    ev = {}
    for day in ("2026-10-01", "2026-10-24", "2026-10-25", "2026-11-23"):
        iid = ctx.add_incident(start_time=f"{day}T12:00:00+05:00")
        ev[day] = ctx.add_evidence(iid, f"{day}/cam1/{iid}.jpg")
    (ctx.evidence_dir / "misc").mkdir()                      # not a day folder
    (ctx.evidence_dir / "misc" / "keep.txt").write_text("x")
    (ctx.evidence_dir / "2026-13-45").mkdir()                # looks like a date, isn't one
    (ctx.evidence_dir / "2026-09-01").write_text("a file")   # old date, but a file, not a folder
    return ev


def marked(ctx):
    return {r["evidence_id"] for r in ctx.sql("SELECT evidence_id FROM evidence WHERE deleted_at IS NOT NULL")}


# ---------------------------------------------------------------- evidence retention

def test_deletes_only_folders_older_than_30_days(ctx, old_evidence):
    n_incidents, n_evidence = len(ctx.sql("SELECT 1 FROM incidents")), len(ctx.sql("SELECT 1 FROM evidence"))

    res = retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)

    assert res.cutoff == "2026-10-25"
    assert res.deleted_folders == ["2026-10-01", "2026-10-24"] and res.failed_folders == []
    assert sorted(p.name for p in ctx.evidence_dir.iterdir()) == ["2026-09-01", "2026-10-25", "2026-11-23",
                                                                  "2026-13-45", "misc"]
    assert (ctx.evidence_dir / "misc" / "keep.txt").exists()
    # Rows are marked, never deleted.
    assert res.evidence_rows_marked == 2
    assert marked(ctx) == {old_evidence["2026-10-01"], old_evidence["2026-10-24"]}
    assert all(r["deleted_at"].endswith("+05:00") for r in ctx.sql(
        "SELECT deleted_at FROM evidence WHERE deleted_at IS NOT NULL"))
    assert len(ctx.sql("SELECT 1 FROM incidents")) == n_incidents
    assert len(ctx.sql("SELECT 1 FROM evidence")) == n_evidence


def test_second_run_changes_nothing(ctx, old_evidence):
    retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)
    before = ctx.sql("SELECT evidence_id, deleted_at FROM evidence ORDER BY evidence_id")
    res = retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)
    assert res.deleted_folders == [] and res.evidence_rows_marked == 0
    assert ctx.sql("SELECT evidence_id, deleted_at FROM evidence ORDER BY evidence_id") == before


def test_deleted_evidence_answers_410(ctx, old_evidence):
    retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)
    h = ctx.headers("manager")
    assert ctx.client.get(f"/api/evidence/{old_evidence['2026-10-24']}/image", headers=h).status_code == 410
    assert ctx.client.get(f"/api/evidence/{old_evidence['2026-10-25']}/image", headers=h).status_code == 200


def test_folder_that_cannot_be_deleted_keeps_its_rows(ctx, old_evidence, monkeypatch):
    real_rmtree = shutil.rmtree

    def locked(path, *a, **kw):
        if str(path).endswith("2026-10-01"):
            raise PermissionError("file in use")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(retention.shutil, "rmtree", locked)
    res = retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)
    assert res.deleted_folders == ["2026-10-24"] and res.failed_folders == ["2026-10-01"]
    assert marked(ctx) == {old_evidence["2026-10-24"]}  # 2026-10-01 still on disk, still viewable
    monkeypatch.setattr(retention.shutil, "rmtree", real_rmtree)
    retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)  # retried next night
    assert old_evidence["2026-10-01"] in marked(ctx)


def test_rows_of_folders_already_gone_are_marked(ctx):
    iid = ctx.add_incident(start_time="2026-09-10T08:00:00+05:00")
    eid = ctx.add_evidence(iid, f"2026-09-10/cam1/{iid}.jpg", write_file=False)  # folder removed by hand
    res = retention.run_retention(ctx.Session, ctx.evidence_dir, today=TODAY)
    assert res.deleted_folders == [] and res.evidence_rows_marked == 1 and marked(ctx) == {eid}


def test_missing_evidence_dir_is_fine(ctx, tmp_path):
    res = retention.run_retention(ctx.Session, tmp_path / "nope", today=TODAY)
    assert res.deleted_folders == []


# ---------------------------------------------------------------- backups

def test_backup_is_a_complete_copy(ctx, tmp_path):
    ctx.add_incident()
    res = retention.backup_database(ctx.db_path, tmp_path / "backups", today=TODAY)
    assert res.backup_file == "rads_2026-11-24.db" and res.error is None
    copy = sqlite3.connect(tmp_path / "backups" / "rads_2026-11-24.db")
    try:
        assert copy.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
        assert copy.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 9
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        copy.close()


def test_keeps_last_seven_and_replaces_same_day(ctx, tmp_path):
    b = tmp_path / "backups"
    b.mkdir()
    for d in range(10, 18):  # 8 older backups
        (b / f"rads_2026-11-{d}.db").write_text("old")
    (b / "notes.txt").write_text("not a backup")

    res = retention.backup_database(ctx.db_path, b, today=TODAY)
    names = sorted(p.name for p in b.iterdir())
    assert names == ["notes.txt"] + [f"rads_2026-11-{d}.db" for d in range(12, 18)] + ["rads_2026-11-24.db"]
    assert res.removed_backups == ["rads_2026-11-10.db", "rads_2026-11-11.db"]

    res = retention.backup_database(ctx.db_path, b, today=TODAY)  # manual re-run the same day
    assert res.removed_backups == [] and len(list(b.glob("rads_*.db"))) == 7


def test_backup_failure_is_reported_not_raised(ctx, tmp_path):
    res = retention.backup_database(tmp_path / "missing.db", tmp_path / "backups", today=TODAY)
    assert res.backup_file is None and res.error


# ---------------------------------------------------------------- daily job + manual trigger + schedule

def test_daily_job_writes_audit_row(ctx, old_evidence, tmp_path):
    out = retention.run_daily_maintenance(ctx.Session, ctx.evidence_dir, ctx.db_path, tmp_path / "backups",
                                          today=TODAY)
    assert out["retention"]["deleted_folders"] == ["2026-10-01", "2026-10-24"]
    assert out["backup"]["backup_file"] == "rads_2026-11-24.db"
    [row] = ctx.audit("maintenance_run")
    assert row["user_id"] is None and row["target_type"] == "system"
    assert row["details"]["retention"]["evidence_rows_marked"] == 2


def test_admin_can_run_it_now(ctx):
    iid = ctx.add_incident(start_time="2000-01-01T12:00:00+05:00")
    eid = ctx.add_evidence(iid, f"2000-01-01/cam1/{iid}.jpg")
    r = ctx.client.post("/api/admin/maintenance/run", headers=ctx.headers("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retention"]["deleted_folders"] == ["2000-01-01"]
    today = datetime.now(TZ).date().isoformat()
    assert body["backup"]["backup_file"] == f"rads_{today}.db"
    assert (ctx.db_path.parent / "backups" / f"rads_{today}.db").exists()
    assert not (ctx.evidence_dir / "2000-01-01").exists() and marked(ctx) == {eid}
    assert ctx.audit("maintenance_run")[0]["user_id"] == ctx.ids["admin"]


@pytest.mark.parametrize("user", ["manager", "tech"])
def test_only_admin_can_run_it(ctx, user):
    assert ctx.client.post("/api/admin/maintenance/run", headers=ctx.headers(user)).status_code == 403
    assert ctx.client.post("/api/admin/maintenance/run").status_code == 401


def test_scheduled_daily_at_3am_pakistan_time(ctx):
    sched = BackgroundScheduler(timezone="Asia/Karachi")
    add_jobs(sched, ctx.Session, get_settings())
    job = sched.get_job("maintenance")
    nxt = job.trigger.get_next_fire_time(None, datetime(2026, 11, 24, 14, 0, tzinfo=TZ))
    assert (nxt.date(), nxt.hour, nxt.minute) == (date(2026, 11, 25), 3, 0)
    assert nxt.utcoffset().total_seconds() == 5 * 3600
    assert job.func is retention.run_daily_maintenance
    assert sched.get_job("escalation") is not None
