from worker.linking import link_parts
from worker.tracking import TrackedPerson

# person box 100..200 x 100..400 (height 300): top third y 100..200, middle third 200..300
P1 = TrackedPerson(1, (100.0, 100.0, 200.0, 400.0), 0.9)


def det(name, cx, cy, conf=0.8, half=10):
    return {"cls_name": name, "conf": conf, "xyxy": (cx - half, cy - half, cx + half, cy + half)}


def only(workers, track_id):
    (w,) = [w for w in workers if w.track_id == track_id]
    return w


def test_person_without_parts_has_all_none():
    (w,) = link_parts([P1], [])
    assert (w.track_id, w.head, w.mask, w.apron) == (1, None, None, None)
    assert w.confs == {"person": 0.9}
    assert w.violations() == []


def test_head_and_mask_in_top_third_are_linked():
    (w,) = link_parts([P1], [det("head_uncovered", 150, 130, 0.7), det("mask_on", 150, 180, 0.6)])
    assert w.head == "uncovered" and w.confs["head"] == 0.7
    assert w.mask == "on" and w.confs["mask"] == 0.6
    assert w.violations() == ["head"]


def test_head_below_top_third_is_not_linked():
    (w,) = link_parts([P1], [det("head_covered", 150, 250), det("mask_off", 150, 350)])
    assert w.head is None and w.mask is None


def test_apron_only_in_middle_third():
    (w,) = link_parts([P1], [det("no_apron", 150, 250)])
    assert w.apron == "no"
    (w,) = link_parts([P1], [det("apron", 150, 150)])  # top third: not an apron position
    assert w.apron is None
    (w,) = link_parts([P1], [det("apron", 150, 350)])  # bottom third
    assert w.apron is None


def test_part_outside_every_person_is_dropped():
    (w,) = link_parts([P1], [det("head_uncovered", 500, 130), det("apron", 210, 250)])
    assert w.head is None and w.apron is None


def test_overlapping_workers_smaller_box_wins():
    big = TrackedPerson(1, (50.0, 50.0, 400.0, 800.0), 0.9)    # head band y 50..300
    small = TrackedPerson(2, (100.0, 100.0, 200.0, 400.0), 0.9)  # head band y 100..200
    ws = link_parts([big, small], [det("head_covered", 150, 150)])
    assert only(ws, 2).head == "covered"
    assert only(ws, 1).head is None
    # a head only the big box qualifies for goes to the big box
    ws = link_parts([big, small], [det("head_uncovered", 300, 150)])
    assert only(ws, 1).head == "uncovered" and only(ws, 2).head is None


def test_most_confident_part_of_a_kind_wins():
    dets = [det("head_covered", 150, 140, 0.5), det("head_uncovered", 150, 150, 0.8), det("head_covered", 150, 160, 0.6)]
    (w,) = link_parts([P1], dets)
    assert w.head == "uncovered" and w.confs["head"] == 0.8


def test_other_classes_are_ignored():
    (w,) = link_parts([P1], [det("phone", 150, 150), det("person", 150, 250), det("bin_open", 150, 150)])
    assert (w.head, w.mask, w.apron) == (None, None, None)


def test_no_persons():
    assert link_parts([], [det("head_uncovered", 150, 150)]) == []


def test_parts_of_an_ignored_person_are_dropped_with_them():
    worker = TrackedPerson(1, (1900.0, 100.0, 2500.0, 1400.0), 0.9)   # head band y 100..533
    customer = (2150.0, 60.0, 2300.0, 540.0)                          # smaller box, head band y 60..220
    head = det("head_uncovered", 2220, 120)
    dropped = []
    (w,) = link_parts([worker], [head], ignored=[customer], dropped=dropped)
    assert w.head is None and dropped == [head]                        # never lands on the worker


def test_ignored_person_elsewhere_does_not_take_parts():
    head = det("head_covered", 150, 130)
    dropped = []
    (w,) = link_parts([P1], [head], ignored=[(1000.0, 0.0, 1200.0, 400.0)], dropped=dropped)
    assert w.head == "covered" and dropped == []
