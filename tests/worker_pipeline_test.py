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


def test_restart_camera_closes_incidents_and_resets_tracker_and_rules():
    from shared.contract import TiedTo
    from worker.main import restart_camera
    from worker.rules import IncidentOpen, RuleEngine
    from worker.settings import Hazard, Rule

    hazards = {"H-01": Hazard("H-01", "Head cover missing", "head_uncovered", "head_covered", TiedTo.WORKER)}
    rules = [Rule(1, "H-01", 2, True, 0.45, 5.0, 0.7, 5.0, 60.0, "Medium", (2,))]
    p = CameraPipeline(2, ZONES, fps=5.0, object_classes=set())
    e = RuleEngine(2, rules, hazards, 5.0)
    worker_dets = [det("person", WORKER), det("head_uncovered", (1770, 390, 1830, 450))]

    def run(n, t0):
        opens = []
        for i in range(n):
            r, _ = p.process(worker_dets, SHAPE, 0.0)
            opens += [ev for ev in e.update(t0 + i * 0.2, r.workers, r.objects, r.lost_track_ids)
                      if isinstance(ev, IncidentOpen)]
        return opens, r

    opens, r = run(20, 1000.0)
    assert len(opens) == 1 and r.workers[0].track_id == 1
    closes = restart_camera(p, e)                       # the clip looped
    assert len(closes) == 1 and closes[0].reason == "clip looped" and closes[0].end_t == 1000.0 + 19 * 0.2
    opens, r = run(20, 2000.0)                          # next pass: fresh tracker ids, fresh incident
    assert r.workers[0].track_id == 1 and len(opens) == 1



def test_crossing_check_switch_is_opt_in():
    from shared.contract import TiedTo
    from worker.main import build_engines, parse_args
    from worker.settings import Camera, Hazard, Rule, WorkerSettings

    assert parse_args([]).crossing_check is False
    assert parse_args(["--crossing-check"]).crossing_check is True
    ws = WorkerSettings(
        cameras={2: Camera(2, "Camera 2", "rtsp://x", True, 5.0, None, None)},
        zones={z.zone_id: z for z in ZONES},
        hazards={"H-06": Hazard("H-06", "Apron missing", "no_apron", "apron", TiedTo.WORKER)},
        rules={1: Rule(1, "H-06", 2, True, 0.45, 5.0, 0.7, 5.0, 60.0, "Medium", (2,))})
    pipelines = {2: CameraPipeline(2, ZONES, fps=5.0, object_classes=set())}
    assert build_engines(ws, pipelines, 5.0)[2].crossing_check is False
    assert build_engines(ws, pipelines, 5.0, crossing_check=True)[2].crossing_check is True
