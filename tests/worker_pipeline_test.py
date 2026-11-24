"""CameraPipeline end to end (ignore policy -> tracking -> linking -> zones) on camera 2's real zones."""
from shared.contract import ZoneType
from worker.main import CameraPipeline
from worker.settings import Zone

FULL = ((0, 0), (2688, 0), (2688, 1520), (0, 1520))
ZONES = [
    Zone(2, 2, "Prep area 2", ZoneType.PREP, FULL, True),
    Zone(4, 2, "Outside (customer side)", ZoneType.IGNORE,
         ((1600, 0), (2545, 0), (2535, 220), (2470, 640), (2080, 555), (1755, 462), (1600, 413)), True),
]
SHAPE = (1520, 2688)
WORKER = (1700.0, 350.0, 1900.0, 1520.0)   # in front of the shutter, cut off by the bottom of the frame
CUSTOMER = (2150.0, 60.0, 2300.0, 540.0)   # beyond the order counter


def det(name, box, conf=0.9):
    return {"cls_name": name, "conf": conf, "xyxy": box}


def frame_dets():
    return [
        det("person", WORKER),
        det("head_uncovered", (1770, 390, 1830, 450)),   # centres inside the ignore polygon, worker's feet outside
        det("mask_off", (1775, 430, 1825, 470)),
        det("no_apron", (1750, 700, 1850, 800)),
        det("phone", (1780, 380, 1820, 440)),            # held by the worker, in the ignore polygon
        det("person", CUSTOMER),
        det("head_uncovered", (2190, 80, 2260, 140)),    # the customer's head
        det("phone", (2200, 300, 2240, 340)),            # held by the customer
        det("phone", (2380, 280, 2420, 320)),            # loose, in the ignore zone
    ]


def test_worker_in_front_of_shutter_keeps_parts_and_customer_is_ignored():
    p = CameraPipeline(2, ZONES, fps=5.0, object_classes={"phone", "bin_open", "bin_closed"})
    r, new_ids = p.process(frame_dets(), SHAPE, capture_t=0.0)

    (w,) = r.workers
    assert w.box == WORKER and new_ids == 1
    assert (w.head, w.mask, w.apron) == ("uncovered", "off", "no")   # parts kept and linked
    assert w.zone_ids == (2,)                                         # feet on the bottom edge: in prep
    assert r.in_prep_count(p.prep_ids) == 1

    assert [d["xyxy"] for d in r.ignored_persons] == [CUSTOMER]
    assert [d["cls_name"] for d in r.dropped_parts] == ["head_uncovered"]   # the customer's head went with him
    assert sorted(d["xyxy"] for d in r.removed) == [(2200, 300, 2240, 340), (2380, 280, 2420, 320)]
    assert [(o["xyxy"], z) for o, z in r.objects] == [((1780, 380, 1820, 440), (2,))]  # worker's phone, in prep
    assert all(d["xyxy"] != CUSTOMER for d in r.dets)
