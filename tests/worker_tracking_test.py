import pytest

from worker.tracking import PersonTracker

SHAPE = (1520, 2688)


def person(x, y=300, w=150, h=400, conf=0.9):
    return {"cls_name": "person", "conf": conf, "xyxy": (float(x), float(y), float(x + w), float(y + h))}


def run(tracker, frames):
    return [tracker.update(dets, SHAPE) for dets in frames]


def test_keep_value_is_5_seconds_at_5_fps():
    t = PersonTracker(1, fps=5.0, keep_s=5.0)
    assert t.track_buffer == 25
    assert t.max_lost_frames == 25
    assert t.keep_s_effective == pytest.approx(5.0)
    t10 = PersonTracker(1, fps=10.0, keep_s=5.0)
    assert t10.max_lost_frames == 50 and t10.keep_s_effective == pytest.approx(5.0)


def test_moving_person_keeps_one_id_and_detection_box():
    t = PersonTracker(1, fps=5.0)
    out = run(t, [[person(100 + 8 * i)] for i in range(30)])
    ids = {p.track_id for frame in out for p in frame}
    assert ids == {1}
    assert out[-1][0].box == person(100 + 8 * 29)["xyxy"]  # the detection box, not the Kalman box


def test_only_person_detections_are_tracked():
    t = PersonTracker(1, fps=5.0)
    other = {"cls_name": "phone", "conf": 0.9, "xyxy": (500.0, 500.0, 540.0, 560.0)}
    out = run(t, [[person(100), other]] * 3)
    assert [len(f) for f in out] == [1, 1, 1]
    assert out[-1][0].box == person(100)["xyxy"]


def test_empty_frames_are_fine():
    t = PersonTracker(1, fps=5.0)
    assert run(t, [[]] * 5) == [[]] * 5


def test_lost_person_within_buffer_keeps_id():
    t = PersonTracker(1, fps=5.0)
    run(t, [[person(100)]] * 5)
    run(t, [[]] * 24)                       # 24 frames = 4.8 s unseen
    (p,) = t.update([person(100)], SHAPE)
    assert p.track_id == 1


def test_lost_person_beyond_buffer_gets_new_id():
    t = PersonTracker(1, fps=5.0)
    run(t, [[person(100)]] * 5)
    run(t, [[]] * 27)                       # > 25 frames unseen: removed
    out = run(t, [[person(100)]] * 2)       # a new track is confirmed on its 2nd frame
    assert out[-1][0].track_id != 1


def test_two_workers_two_ids():
    t = PersonTracker(1, fps=5.0)
    out = run(t, [[person(100 + 5 * i), person(1500 - 5 * i)] for i in range(10)])
    assert sorted(p.track_id for p in out[-1]) == [1, 2]


def test_cameras_have_independent_ids():
    a, b = PersonTracker(1, fps=5.0), PersonTracker(2, fps=5.0)
    run(a, [[person(100), person(800)]] * 3)
    out_b = run(b, [[person(400)]] * 3)
    assert out_b[-1][0].track_id == 1        # camera 2 starts at 1 regardless of camera 1


def test_bad_fps():
    with pytest.raises(ValueError):
        PersonTracker(1, fps=0)
