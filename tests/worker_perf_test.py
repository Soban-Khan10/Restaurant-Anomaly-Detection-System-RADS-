import pytest

from worker.perf import PerfWindow, advance_slot


def test_advance_slot_on_time():
    assert advance_slot(10.0, 10.05, 0.2) == (10.0, 0)
    assert advance_slot(10.0, 9.0, 0.2) == (10.0, 0)  # early: caller sleeps


def test_advance_slot_one_missed():
    slot, skipped = advance_slot(10.0, 10.25, 0.2)
    assert skipped == 1
    assert slot == pytest.approx(10.2)


def test_advance_slot_several_missed():
    slot, skipped = advance_slot(10.0, 11.05, 0.2)
    assert skipped == 5
    assert slot == pytest.approx(11.0)
    # the returned slot is current: advancing again skips nothing
    assert advance_slot(slot, 11.05, 0.2) == (slot, 0)


def test_advance_slot_bad_period():
    with pytest.raises(ValueError):
        advance_slot(0.0, 1.0, 0)


def test_perf_window_summary_and_reset():
    w = PerfWindow([1, 2], start_t=100.0)
    for i in range(1, 11):  # 10 frames on cam 1: total 10..100 ms
        w.record(1, detect_ms=float(i), total_ms=10.0 * i, lag_ms=20.0 * i)
    w.skip(1, 2)
    w.stale(2)
    w.error(2)
    s = w.summary(now=110.0)  # 10 s wall
    c1, c2 = s["cams"][1], s["cams"][2]
    assert c1["processed"] == 10 and c1["skipped"] == 2
    assert c1["detect_ms"]["mean"] == 5.5
    assert c1["total_ms"]["max"] == 100.0
    assert c1["lag_ms"]["p50"] == 110.0
    assert c1["lag_ms"]["p95"] == pytest.approx(191.0)
    assert c2["processed"] == 0 and c2["stale"] == 1 and c2["errors"] == 1
    assert c2["lag_ms"]["mean"] is None
    assert s["busy_pct"] == pytest.approx(100 * 0.55 / 10, abs=0.05)  # 550 ms busy in 10 s

    w.add_gui(0.5)
    s = w.summary(now=110.0)
    assert s["gui_pct"] == pytest.approx(5.0)
    assert s["busy_pct"] == pytest.approx(5.5, abs=0.05)  # GUI time is not counted as busy

    w.reset(200.0)
    s = w.summary(now=230.0)
    assert s["cams"][1]["processed"] == 0 and s["busy_pct"] == 0.0 and s["gui_pct"] == 0.0
    assert s["wall_s"] == 30.0
