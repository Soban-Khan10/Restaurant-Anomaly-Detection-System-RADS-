"""Rule engine on synthetic observation sequences (5 FPS, times in seconds)."""
import pytest

from shared.contract import TiedTo
from worker.linking import Worker
from worker.rules import IncidentClose, IncidentOpen, RuleEngine, Spot, crossing_window, match_spots, min_seen
from worker.settings import Hazard, Rule

FPS = 5.0
DT = 1.0 / FPS
T0 = 1_800_000_000.0  # epoch seconds

HAZARDS = {
    "H-01": Hazard("H-01", "Head cover missing", "head_uncovered", "head_covered", TiedTo.WORKER),
    "H-04": Hazard("H-04", "Phone in preparation area", "phone", None, TiedTo.ZONE),
    "H-05": Hazard("H-05", "Bin left open", "bin_open", "bin_closed", TiedTo.ZONE),
    "H-06": Hazard("H-06", "Apron missing", "no_apron", "apron", TiedTo.WORKER),
    "H-08": Hazard("H-08", "Mask missing", "mask_off", "mask_on", TiedTo.WORKER),
}
PREP, WASTE = 1, 3


def rule(hazard_id, rule_id=1, zone_ids=(PREP,), **kw):
    v = dict(enabled=True, conf_threshold=0.45, window_s=5.0, min_ratio=0.70, end_gap_s=5.0, cooldown_s=60.0,
             severity="Medium")
    v.update(kw)
    return Rule(rule_id, hazard_id, 1, v["enabled"], v["conf_threshold"], v["window_s"], v["min_ratio"],
                v["end_gap_s"], v["cooldown_s"], v["severity"], tuple(zone_ids))


H04_DB = dict(window_s=10.0, min_ratio=0.30, end_gap_s=60.0, cooldown_s=300.0)  # the values in the local DB


def worker(tid, head=None, conf=0.8, box=(100.0, 100.0, 300.0, 700.0), zones=(PREP,), mask=None, apron=None):
    w = Worker(tid, box, head=head, mask=mask, apron=apron, confs={"person": 0.9}, zone_ids=tuple(zones))
    if apron:
        w.confs["apron"] = conf
        w.parts["apron"] = {"cls_name": "apron" if apron == "yes" else "no_apron", "conf": conf,
                            "xyxy": (box[0] + 20, box[1] + 250, box[2] - 20, box[1] + 400)}
    if head:
        w.confs["head"] = conf
        w.parts["head"] = {"cls_name": f"head_{head}", "conf": conf, "xyxy": (150.0, 110.0, 250.0, 200.0)}
    if mask:
        w.confs["mask"] = conf
    return w


def phone(cx, cy, conf=0.6, zones=(PREP,)):
    return ({"cls_name": "phone", "conf": conf, "xyxy": (cx - 20.0, cy - 30.0, cx + 20.0, cy + 30.0)}, tuple(zones))


def bin_(state, conf=0.8, zones=(WASTE,)):
    return ({"cls_name": f"bin_{state}", "conf": conf, "xyxy": (1700.0, 700.0, 1900.0, 1000.0)}, tuple(zones))


class Run:
    """Feeds frames at 5 FPS and collects events."""

    def __init__(self, *rules, **engine_kw):
        self.engine = RuleEngine(1, list(rules), HAZARDS, FPS, **engine_kw)
        self.t = T0
        self.events = []

    def frame(self, workers=(), objects=(), lost=()):
        self.events += self.engine.update(self.t, list(workers), list(objects), list(lost))
        self.t += DT

    def frames(self, n, workers=(), objects=(), lost=()):
        for _ in range(n):
            self.frame(workers, objects, lost)

    @property
    def opens(self):
        return [e for e in self.events if isinstance(e, IncidentOpen)]

    @property
    def closes(self):
        return [e for e in self.events if isinstance(e, IncidentClose)]


def test_min_seen():
    assert min_seen(rule("H-01"), FPS) == 13              # 13 of 25 for a 5 s window
    assert min_seen(rule("H-04", **H04_DB), FPS) == 25    # 25 of 50 for H-04's 10 s window


# --------------------------------------------------------------------------- the four from the spec


def test_flicker_below_70_percent_gives_no_incident():
    r = Run(rule("H-01"))
    for i in range(600):  # 2 minutes, 3 of 5 frames uncovered = 60 %
        r.frame([worker(1, "uncovered" if i % 5 < 3 else "covered")])
    assert r.opens == []


def test_steady_violation_gives_exactly_one_incident():
    r = Run(rule("H-01"))
    r.frames(600, [worker(1, "uncovered")])  # 2 minutes
    assert len(r.opens) == 1
    o = r.opens[0]
    assert (o.hazard_id, o.track_id, o.zone_id) == ("H-01", 1, PREP)
    assert o.start_t == T0                                 # first violation inside the confirming window
    assert o.t == pytest.approx(T0 + 12 * DT)              # confirmed at the 13th frame
    assert o.trigger["cls_name"] == "head_uncovered" and o.subject_box == (100.0, 100.0, 300.0, 700.0)
    assert r.closes == []


def test_violation_again_within_cooldown_gives_no_new_incident():
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered")])                 # 10 s -> incident
    r.frames(40, [worker(1, "covered")])                   # fixed -> closes after end_gap 5 s
    assert len(r.opens) == 1 and len(r.closes) == 1
    c = r.closes[0]
    assert c.reason == "compliant" and c.end_t == pytest.approx(T0 + 49 * DT)  # the last violation
    r.frames(100, [worker(1, "uncovered")])                # 20 s later violating again, still in cooldown
    assert len(r.opens) == 1
    r.frames(250, [worker(1, "uncovered")])                # keeps violating after the 60 s cooldown
    assert len(r.opens) == 2


def test_h04_at_35_percent_gives_incident():
    r = Run(rule("H-04", **H04_DB))
    for i in range(100):  # 20 s, phone in 7 of 20 frames = 35 %
        r.frame(objects=[phone(1000, 800)] if i % 20 < 7 else [])
    assert len(r.opens) == 1
    assert r.opens[0].track_id is None and r.opens[0].zone_id == PREP


def test_h04_at_25_percent_gives_no_incident():
    r = Run(rule("H-04", **H04_DB))
    for i in range(300):
        r.frame(objects=[phone(1000, 800)] if i % 4 == 0 else [])
    assert r.opens == []


# --------------------------------------------------------------------------- H-04 phone spots


def test_counter_phone_flickering_for_10_minutes_gives_one_incident():
    r = Run(rule("H-04", **H04_DB))
    for i in range(3000):  # 10 minutes; ~47 % of frames, with gaps of up to 40 s (< end_gap 60 s)
        in_long_gap = (i // 750) % 2 == 1 and i % 750 < 200
        seen = (i % 15 < 7) and not in_long_gap
        jitter = (i % 3) * 4
        r.frame(objects=[phone(1672 + jitter, 489 - jitter, conf=0.5)] if seen else [])
    assert len(r.opens) == 1
    assert r.closes == []
    assert len(r.engine.spots("H-04", PREP)) == 1


def test_second_phone_600px_away_while_first_is_open_gives_second_incident():
    r = Run(rule("H-04", **H04_DB))
    r.frames(100, objects=[phone(1672, 489)])               # counter phone -> incident 1
    assert len(r.opens) == 1
    r.frames(75, objects=[phone(1672, 489), phone(1072, 489)])  # 15 s: a second phone 600 px away
    assert len(r.opens) == 2
    a, b = r.opens
    assert a.spot_id != b.spot_id and a.zone_id == b.zone_id == PREP
    assert r.closes == []


def test_phone_carried_slowly_across_the_frame_gives_one_incident():
    r = Run(rule("H-04", **H04_DB))
    for i in range(200):  # 40 s, 12 px per frame (60 px/s) from x=100 to x=2500
        r.frame(objects=[phone(100 + 12 * i, 900)])
    assert len(r.opens) == 1
    assert len(r.engine.spots("H-04", PREP)) == 1


def test_spot_is_forgotten_after_end_gap_plus_cooldown():
    r = Run(rule("H-04", **H04_DB))
    r.frames(100, objects=[phone(1000, 800)])               # incident
    r.frames(5 * 60, objects=[])                             # 60 s without -> closes ("compliant")
    assert len(r.closes) == 1 and r.closes[0].reason == "compliant"
    assert len(r.engine.spots("H-04", PREP)) == 1
    r.frames(5 * 305, objects=[])                            # 360 s since the last detection
    assert r.engine.spots("H-04", PREP) == []


def test_phone_back_at_same_spot_within_cooldown_gives_no_new_incident():
    r = Run(rule("H-04", **H04_DB))
    r.frames(100, objects=[phone(1000, 800)])
    r.frames(5 * 61, objects=[])                             # closed
    r.frames(5 * 60, objects=[phone(1010, 795)])             # back 61 s later, in the 300 s cooldown
    assert len(r.opens) == 1


def test_match_spots_duplicates_and_new():
    spots = [Spot(1, PREP, (1000.0, 800.0), T0)]
    dets = [phone(1010, 800, 0.4)[0], phone(1030, 790, 0.7)[0], phone(1700, 800, 0.5)[0], phone(1720, 800, 0.6)[0]]
    matched, new = match_spots(spots, dets)
    assert matched[1]["conf"] == 0.7                         # two boxes on one phone: the more confident one
    assert len(new) == 1 and new[0]["conf"] == 0.6           # one new phone (its two boxes merged)


# --------------------------------------------------------------------------- workers


def test_two_workers_violating_at_the_same_time_give_two_incidents():
    r = Run(rule("H-01"))
    a = worker(1, "uncovered", box=(100.0, 100.0, 300.0, 700.0))
    b = worker(2, "uncovered", box=(320.0, 100.0, 520.0, 700.0))  # right next to each other
    r.frames(100, [a, b])
    assert sorted(o.track_id for o in r.opens) == [1, 2]


def test_id_switch_gives_no_new_incident():
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered", box=(100.0, 100.0, 300.0, 700.0))])
    assert len(r.opens) == 1
    r.frame()                                                # one frame with nobody (track 1 lost)
    r.frames(25, [worker(7, "uncovered", box=(110.0, 105.0, 310.0, 705.0))])  # new ID at the same spot
    r.frame([worker(7, "uncovered", box=(110.0, 105.0, 310.0, 705.0))], lost=[1])  # tracker drops track 1
    r.frames(200, [worker(7, "uncovered", box=(110.0, 105.0, 310.0, 705.0))])
    assert len(r.opens) == 1
    assert r.closes == []                                    # the incident continues on track 7
    r.frames(40, [worker(7, "covered", box=(110.0, 105.0, 310.0, 705.0))])
    assert len(r.closes) == 1 and r.closes[0].ref == r.opens[0].ref


def test_id_switch_during_cooldown_gives_no_new_incident():
    r = Run(rule("H-01"))
    box = (100.0, 100.0, 300.0, 700.0)
    r.frames(50, [worker(1, "uncovered", box=box)])
    r.frames(10, [])
    r.frame(lost=[1])                                        # dropped -> incident closes, cooldown starts
    assert [c.reason for c in r.closes] == ["track lost"]
    r.frames(100, [worker(9, "uncovered", box=(140.0, 100.0, 340.0, 700.0))])  # 20 s later, close by
    assert len(r.opens) == 1


A_BOX = (100.0, 100.0, 300.0, 700.0)
B_BOX = (350.0, 100.0, 550.0, 700.0)


def test_swap_while_overlapping_gives_no_new_incident():
    """Head-count guard: A hides behind B, the tracker gives A a new ID far from A's last box (so the position
    guard cannot match); one out, one in, still 2 people -> the new track is A."""
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered", box=A_BOX), worker(2, "uncovered", box=B_BOX)])
    assert len(r.opens) == 2
    ref_a = next(o.ref for o in r.opens if o.track_id == 1)
    r.frames(4, [worker(2, "uncovered", box=(300.0, 100.0, 500.0, 700.0))])          # A hidden behind B
    far = (600.0, 100.0, 800.0, 700.0)                                                # 500 px from A's last box
    r.frames(10, [worker(2, "uncovered", box=B_BOX), worker(8, "uncovered", box=far)])
    r.frame([worker(2, "uncovered", box=B_BOX), worker(8, "uncovered", box=far)], lost=[1])  # tracker drops 1
    r.frames(100, [worker(2, "uncovered", box=B_BOX), worker(8, "uncovered", box=far)])
    assert len(r.opens) == 2 and r.closes == []
    assert r.engine.takeovers == 1
    r.frames(30, [worker(2, "uncovered", box=B_BOX), worker(8, "covered", box=far)])  # track 8 (= A) complies
    assert [c.ref for c in r.closes] == [ref_a]                                       # A's original incident


def test_new_worker_entering_while_the_other_stays_visible_gets_own_incident():
    r = Run(rule("H-01"))
    r.frames(60, [worker(1, "uncovered", box=A_BOX)])
    r.frames(60, [worker(1, "uncovered", box=A_BOX), worker(2, "uncovered", box=B_BOX)])
    assert sorted(o.track_id for o in r.opens) == [1, 2]
    assert r.engine.takeovers == 0


def test_different_worker_entering_more_than_5s_after_one_left_gets_own_incident():
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered", box=A_BOX)])
    r.frames(26, [])
    r.frame(lost=[1])                                         # dropped after 5 s -> incident closes
    r.frames(3, [])
    r.frames(60, [worker(5, "uncovered", box=(1500.0, 100.0, 1700.0, 700.0))])  # 6 s after A was last seen
    assert [o.track_id for o in r.opens] == [1, 5]
    assert r.engine.takeovers == 0


def test_several_swaps_pair_by_nearest_last_box():
    r = Run(rule("H-01"))
    a, b = A_BOX, (1000.0, 100.0, 1200.0, 700.0)
    r.frames(50, [worker(1, "uncovered", box=a), worker(2, "uncovered", box=b)])
    ref = {o.track_id: o.ref for o in r.opens}
    r.frame()                                                  # both lost for a moment
    a2, b2 = (450.0, 100.0, 650.0, 700.0), (1250.0, 100.0, 1450.0, 700.0)  # > 1 box width away: head count only
    r.frames(20, [worker(9, "uncovered", box=a2), worker(8, "uncovered", box=b2)])
    assert len(r.opens) == 2 and r.engine.takeovers == 2
    r.frames(30, [worker(9, "covered", box=a2), worker(8, "uncovered", box=b2)])
    assert [c.ref for c in r.closes] == [ref[1]]                # track 9 continued worker 1, not worker 2


def test_clip_restart_closes_open_incidents_and_next_pass_is_fresh():
    r = Run(rule("H-01"), rule("H-04", rule_id=2, **H04_DB))
    r.frames(100, [worker(1, "uncovered")], objects=[phone(1000, 800)])
    assert len(r.opens) == 2
    last_t = r.t - DT
    closes = r.engine.restart()
    assert len(closes) == 2 and {c.reason for c in closes} == {"clip looped"}
    assert all(c.end_t == pytest.approx(last_t) for c in closes)   # end = last violation
    assert r.engine.open_incidents() == [] and r.engine.spots("H-04", PREP) == []
    r.frames(100, [worker(1, "uncovered")], objects=[phone(1000, 800)])  # next pass: same scene again
    assert len(r.opens) == 4                                    # fresh incidents, no cooldown carried over


def test_worker_unseen_mid_incident_stays_open_then_compliant_closes():
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered")])
    r.frames(50, [worker(1, None)])                         # 10 s: visible, head not seen
    r.frames(50, [])                                         # 10 s: not visible at all (lost, not dropped)
    assert len(r.opens) == 1 and r.closes == []
    r.frames(int(5.0 * FPS) + 1, [worker(1, "covered")])    # compliant for end_gap_s
    assert len(r.closes) == 1 and r.closes[0].reason == "compliant"


def test_worker_leaving_the_zone_closes():
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered")])
    r.frame([worker(1, "uncovered", zones=())])
    assert [c.reason for c in r.closes] == ["left zone"]


def test_below_threshold_is_unseen_not_compliant():
    r = Run(rule("H-01"))
    r.frames(50, [worker(1, "uncovered")])
    r.frames(100, [worker(1, "covered", conf=0.30)])        # covered but below 0.45: unseen
    assert r.closes == []


def test_disabled_rule_and_other_camera_are_ignored():
    off = rule("H-01", enabled=False)
    r = Run(off)
    r.frames(100, [worker(1, "uncovered")])
    assert r.opens == []


# --------------------------------------------------------------------------- bins


def test_bin_hidden_for_10_s_stays_open():
    r = Run(rule("H-05", zone_ids=(WASTE,)))
    r.frames(50, objects=[bin_("open")])
    assert len(r.opens) == 1
    r.frames(50, objects=[])                                # a worker stands in front of it: unseen
    assert r.closes == []
    r.frames(10, objects=[bin_("open")])
    r.frames(int(5.0 * FPS) + 1, objects=[bin_("closed")])
    assert len(r.closes) == 1 and r.closes[0].reason == "compliant"


def test_bin_higher_confidence_wins():
    r = Run(rule("H-05", zone_ids=(WASTE,)))
    r.frames(50, objects=[bin_("open", 0.5), bin_("closed", 0.9)])
    assert r.opens == []
    r.frames(50, objects=[bin_("open", 0.9), bin_("closed", 0.5)])
    assert len(r.opens) == 1


def test_close_all_on_stop():
    r = Run(rule("H-01"), rule("H-04", rule_id=2, **H04_DB))
    r.frames(100, [worker(1, "uncovered")], objects=[phone(1000, 800)])
    assert len(r.opens) == 2
    closes = r.engine.close_all(r.t)
    assert len(closes) == 2 and all(c.end_t == r.t and c.reason == "worker stopped" for c in closes)
    assert r.engine.open_incidents() == []


# --------------------------------------------------------------------------- crossing check (pure ID swaps)

LEFT, RIGHT = (100.0, 100.0, 300.0, 700.0), (600.0, 100.0, 800.0, 700.0)
MID_L, MID_R = (350.0, 100.0, 550.0, 700.0), (450.0, 100.0, 650.0, 700.0)   # overlapping (IoU > 0)


def cross(r, a1=None, a2=None, n=5):
    """Tracks 1 and 2 overlap for n frames with the given apron states."""
    r.frames(n, [worker(1, box=MID_R, apron=a1), worker(2, box=MID_L, apron=a2)])


def test_pure_id_swap_moves_the_open_incident_instead_of_a_duplicate():
    r = Run(rule("H-06"), crossing_check=True)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])      # K = track 1, no apron
    (o,) = r.opens
    cross(r)
    # the tracker swapped them: track 1 is now T (apron unseen), track 2 is now K (no apron)
    r.frames(60, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 1 and r.closes == []                                  # no duplicate
    assert r.engine.swap_transfers == 1
    r.frames(30, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="yes")])     # K puts an apron on
    assert [c.ref for c in r.closes] == [o.ref]                                 # the original incident closes
    r.frames(60, [worker(1, box=LEFT, apron="no"), worker(2, box=RIGHT, apron="yes")])
    assert len(r.opens) == 2 and r.opens[1].track_id == 1                       # track 1 started fresh


def test_two_genuine_violators_crossing_get_two_incidents():
    r = Run(rule("H-06"), crossing_check=True)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT, apron="yes")])
    cross(r, a1="no")                                                          # A still violating while crossing
    r.frames(60, [worker(1, box=LEFT, apron="no"), worker(2, box=RIGHT, apron="no")])  # B takes his apron off
    assert sorted(o.track_id for o in r.opens) == [1, 2]
    assert r.engine.swap_transfers == 0


def test_worker_fixing_his_apron_while_crossing_a_compliant_worker_closes_normally():
    r = Run(rule("H-06"), crossing_check=True)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT, apron="yes")])
    cross(r, a1="yes", a2="yes")
    r.frames(40, [worker(1, box=LEFT, apron="yes"), worker(2, box=RIGHT, apron="yes")])
    assert len(r.opens) == 1 and [c.reason for c in r.closes] == ["compliant"]
    assert r.engine.swap_transfers == 0


def test_two_candidates_do_nothing_special():
    r = Run(rule("H-06"), crossing_check=True)
    third = (1000.0, 100.0, 1200.0, 700.0)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(3, box=third, apron="no"), worker(2, box=LEFT)])
    assert len(r.opens) == 2
    r.frames(5, [worker(1, box=MID_R), worker(2, box=MID_L), worker(3, box=(500.0, 100.0, 700.0, 700.0))])
    r.frames(60, [worker(1, box=LEFT), worker(3, box=third), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 3 and r.engine.swap_transfers == 0


def test_overlap_older_than_the_crossing_window_does_not_count():
    r = Run(rule("H-06"), crossing_check=True)                                 # window_s 5 -> crossing window 15 s
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])
    cross(r)
    r.frames(85, [worker(1, box=LEFT), worker(2, box=RIGHT)])                  # 17 s, both apron unseen
    r.frames(60, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 2 and r.engine.swap_transfers == 0


def test_two_maskless_workers_brief_overlap_one_mask_unseen_no_transfer():
    """The H-08 case from the clip: both workers maskless from the start; B's mask is seen less often, so B
    confirms later; just before that they brush (brief overlap) while A's mask is unseen for a moment.
    B was already violating before the overlap -> no transfer; each keeps his own incident and snapshot."""
    r = Run(rule("H-08"), crossing_check=True)
    a_box, b_box = RIGHT, LEFT
    for i in range(15):                                        # 3 s: A always maskless, B maskless 2 of 3 frames
        r.frame([worker(1, box=a_box, mask="off"), worker(2, box=b_box, mask="off" if i % 3 != 2 else None)])
    assert [o.track_id for o in r.opens] == [1]                # A confirmed, B not yet
    for _ in range(2):                                         # brief overlap; A's mask unseen for a moment
        r.frame([worker(1, box=MID_R), worker(2, box=MID_L, mask="off")])
    for i in range(30):
        r.frame([worker(1, box=a_box, mask=None if i < 3 else "off"), worker(2, box=b_box, mask="off")])
    assert sorted(o.track_id for o in r.opens) == [1, 2]       # two incidents, one per worker
    assert r.engine.swap_transfers == 0 and r.closes == []
    snap = {o.track_id: o.subject_box for o in r.opens}
    assert snap[1] == a_box and snap[2] == b_box               # each incident's snapshot shows its own worker



def test_crossing_window_follows_window_s():
    assert crossing_window(rule("H-06", window_s=5.0)) == 15.0
    assert crossing_window(rule("H-04", **H04_DB)) == 20.0

    def swap_with_late_confirm(window_s):
        """K's violations show again 11.4 s after the crossing; B confirms 14.8 s (window 5) or 13.2 s (window 2)
        after the first overlap frame."""
        r = Run(rule("H-06", window_s=window_s), crossing_check=True)
        r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])
        cross(r)
        r.frames(57, [worker(1, box=LEFT), worker(2, box=RIGHT)])
        r.frames(30, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
        return r

    r5 = swap_with_late_confirm(5.0)                                           # 14.8 s <= 15 s
    assert len(r5.opens) == 1 and r5.engine.swap_transfers == 1
    r2 = swap_with_late_confirm(2.0)                                           # 13.2 s > 12 s
    assert len(r2.opens) == 2 and r2.engine.swap_transfers == 0


def test_a_violations_during_overlap_frames_are_not_evidence():
    """The swap happens in the middle of the crossing; A's part is still seen while the boxes overlap."""
    r = Run(rule("H-06"), crossing_check=True)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])
    cross(r, a1="no")                                                          # A "violates" only in overlap frames
    r.frames(40, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 1 and r.engine.swap_transfers == 1


def test_b_violations_during_overlap_frames_are_not_evidence():
    """An earlier brush (outside the crossing window but inside B's look-back) where A's no_apron was linked to B's
    box does not count as B violating before the crossing."""
    r = Run(rule("H-06"), crossing_check=True)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])
    cross(r, a1="no", a2="no", n=2)                                            # ambiguous: both boxes "violating"
    r.frames(63, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])      # 12.6 s apart, A still violating
    cross(r)                                                                   # the real crossing (swap)
    r.frames(30, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 1 and r.engine.swap_transfers == 1


def test_crossing_check_is_off_by_default():
    r = Run(rule("H-06"))  # default: off
    assert r.engine.crossing_check is False
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])
    cross(r)
    r.frames(60, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 2 and r.engine.swap_transfers == 0                  # the duplicate is not prevented


def test_crossing_check_explicitly_off():
    r = Run(rule("H-06"), crossing_check=False)
    r.frames(50, [worker(1, box=RIGHT, apron="no"), worker(2, box=LEFT)])
    cross(r)
    r.frames(60, [worker(1, box=LEFT), worker(2, box=RIGHT, apron="no")])
    assert len(r.opens) == 2 and r.engine.swap_transfers == 0
