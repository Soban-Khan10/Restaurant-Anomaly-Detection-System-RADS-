import pytest

from shared.contract import ZoneType
from worker.settings import Zone
from worker.zones import CameraZones, anchor, bottom_centre, centre

PREP = Zone(1, 1, "Prep", ZoneType.PREP, ((0, 0), (1000, 0), (1000, 800), (0, 800)), True)
WASTE = Zone(2, 1, "Bin", ZoneType.WASTE, ((1200, 600), (1500, 600), (1500, 900), (1200, 900)), True)
IGNORE = Zone(3, 1, "Hatch", ZoneType.IGNORE, ((800, 0), (1000, 0), (1000, 300), (800, 300)), True)


def det(name, box, conf=0.9):
    return {"cls_name": name, "conf": conf, "xyxy": box}


def test_anchor_points():
    assert bottom_centre((100, 100, 200, 400)) == (150, 400)
    assert centre((100, 100, 200, 400)) == (150, 250)
    assert anchor(det("person", (100, 100, 200, 400))) == (150, 400)
    assert anchor(det("phone", (100, 100, 200, 400))) == (150, 250)


def test_worker_feet_inside_and_outside():
    z = CameraZones([PREP, WASTE])
    assert z.person_zones((100, 100, 200, 700)) == (1,)       # feet at y 700: inside
    assert z.person_zones((100, 500, 200, 1000)) == ()        # box overlaps the zone, feet at y 1000: outside
    assert z.person_zones((1300, 200, 1400, 850)) == (2,)     # feet in the waste zone


def test_point_on_boundary_counts_as_inside():
    z = CameraZones([PREP])
    assert z.person_zones((900, 100, 1100, 800)) == (1,)      # feet exactly at (1000, 800), the corner


def test_object_uses_box_centre():
    z = CameraZones([PREP, WASTE])
    assert z.object_zones((1250, 650, 1350, 750)) == (2,)
    assert z.object_zones((950, 750, 1150, 950)) == ()         # centre (1050, 850) outside both


def test_ignore_zone_is_not_a_membership_zone():
    z = CameraZones([PREP, IGNORE])
    assert z.object_zones((850, 50, 950, 150)) == (1,)         # inside prep (and ignore); only prep reported
    assert z.zones_at((900, 100), types=(ZoneType.IGNORE,)) == (3,)


# camera 2's real ignore zone (shutter, street window, fan); bottom edge follows the back of the order counter
OUTSIDE = Zone(4, 2, "Outside (customer side)", ZoneType.IGNORE,
               ((1600, 0), (2545, 0), (2535, 220), (2470, 640), (2080, 555), (1755, 462), (1600, 413)), True)
FULL = ((0, 0), (2688, 0), (2688, 1520), (0, 1520))
PREP2 = Zone(2, 2, "Prep area 2", ZoneType.PREP, FULL, True)
WORKER = (1700.0, 350.0, 1900.0, 1300.0)    # in the kitchen in front of the shutter: feet (1800, 1300)
CUSTOMER = (2150.0, 60.0, 2300.0, 540.0)    # beyond the counter: feet (2225, 540) inside the ignore zone


def test_person_feet_inside_and_outside_ignore_zone():
    z = CameraZones([PREP2, OUTSIDE])
    assert z.in_ignore((1800, 420))          # the worker's head area is inside the polygon...
    r = z.apply_ignore([det("person", WORKER), det("person", CUSTOMER)])
    assert [d["xyxy"] for d in r.persons] == [WORKER]          # ...but his feet are not: kept
    assert [d["xyxy"] for d in r.ignored_persons] == [CUSTOMER]


def test_body_parts_are_never_filtered_on_their_own():
    z = CameraZones([PREP2, OUTSIDE])
    head = det("head_uncovered", (1770, 390, 1830, 450))   # centre (1800, 420) inside the ignore zone
    mask = det("mask_off", (1775, 430, 1825, 470))         # centre (1800, 450) inside the ignore zone
    r = z.apply_ignore([det("person", WORKER), head, mask])
    assert r.parts == [head, mask] and r.removed == []


def test_phone_held_by_kept_person_in_ignore_zone_is_kept():
    z = CameraZones([PREP2, OUTSIDE])
    phone = det("phone", (1780, 380, 1820, 440))            # centre (1800, 410): in the ignore zone and the worker's box
    r = z.apply_ignore([det("person", WORKER), phone])
    assert r.objects == [phone] and r.removed == []


def test_loose_phone_in_ignore_zone_is_removed():
    z = CameraZones([PREP2, OUTSIDE])
    phone = det("phone", (2380, 280, 2420, 320))            # centre (2400, 300): ignore zone, nobody around it
    r = z.apply_ignore([det("person", WORKER), phone])
    assert r.removed == [phone] and r.objects == []


def test_phone_held_by_ignored_person_is_removed():
    z = CameraZones([PREP2, OUTSIDE])
    phone = det("phone", (2200, 300, 2240, 340))            # inside the customer's box
    r = z.apply_ignore([det("person", CUSTOMER), phone])
    assert r.removed == [phone] and r.objects == []


def test_phone_goes_with_the_smallest_person_box():
    z = CameraZones([PREP2, OUTSIDE])
    big_worker = (1900.0, 100.0, 2500.0, 1400.0)            # feet (2200, 1400): kitchen; also covers the customer
    phone = det("phone", (2200, 300, 2240, 340))
    r = z.apply_ignore([det("person", big_worker), det("person", CUSTOMER), phone])
    assert r.removed == [phone]                              # the customer (smaller box) holds it


def test_loose_phone_outside_ignore_zone_is_kept():
    z = CameraZones([PREP2, OUTSIDE])
    phone = det("phone", (900, 900, 960, 940))
    assert z.apply_ignore([phone]).objects == [phone]


def test_bins_by_box_centre():
    z = CameraZones([PREP, IGNORE])
    inside = det("bin_open", (880, 80, 920, 120))           # centre (900, 100) in the ignore zone
    outside = det("bin_closed", (100, 100, 200, 200))
    r = z.apply_ignore([inside, outside])
    assert r.removed == [inside] and r.objects == [outside]


def test_no_ignore_zone_keeps_everything():
    z = CameraZones([PREP])
    dets = [det("person", (850, 0, 950, 250)), det("phone", (880, 80, 920, 120)), det("mask_on", (1, 1, 5, 5))]
    r = z.apply_ignore(dets)
    assert r.persons == dets[:1] and r.objects == dets[1:2] and r.parts == dets[2:] and not r.removed


def test_feet_on_bottom_frame_edge_are_in_a_full_frame_zone():
    z = CameraZones([PREP2])
    assert z.person_zones((1000, 600, 1200, 1520)) == (2,)   # cut off by the bottom of the frame: y2 = 1520
    assert z.person_zones((2488, 600, 2688, 1520)) == (2,)    # feet (2588, 1520) near the bottom-right corner
    assert z.zones_at((2688, 1520)) == (2,)                   # the corner itself
    assert z.zones_at((0, 0)) == (2,)
    for t in (ZoneType.WASTE, ZoneType.IGNORE):               # same rule for every zone type
        zz = CameraZones([Zone(9, 2, "x", t, FULL, True)])
        assert zz.zones_at((1344, 1520), types=(t,)) == (9,)


def test_part_class_lists_match():
    from worker.linking import PARTS
    from worker.zones import BODY_PART_CLASSES
    assert set(PARTS) == BODY_PART_CLASSES


def test_inactive_and_degenerate_zones_are_skipped():
    inactive = Zone(4, 1, "Off", ZoneType.PREP, PREP.polygon, False)
    two_pts = Zone(5, 1, "Line", ZoneType.PREP, ((0, 0), (10, 10)), True)
    flat = Zone(6, 1, "Flat", ZoneType.PREP, ((0, 0), (10, 0), (20, 0)), True)
    z = CameraZones([inactive, two_pts, flat, WASTE])
    assert [x.zone_id for x in z.zones] == [2]


def test_check_frame_flags_outside_polygons_and_coverage():
    seeded_prep = Zone(1, 1, "Prep area 1", ZoneType.PREP, ((0, 0), (1920, 0), (1920, 1080), (0, 1080)), True)
    too_big = Zone(2, 1, "Too big", ZoneType.PREP, ((0, 0), (3000, 0), (3000, 1520), (0, 1520)), True)
    checks = {c.zone_id: c for c in CameraZones([seeded_prep, too_big]).check_frame(2688, 1520)}
    assert not checks[1].outside
    assert checks[1].coverage_pct == pytest.approx(50.8, abs=0.1)
    assert checks[2].outside
    assert checks[2].coverage_pct == pytest.approx(100.0)
