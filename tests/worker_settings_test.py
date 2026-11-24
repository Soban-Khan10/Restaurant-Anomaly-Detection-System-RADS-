import sqlite3

import pytest

from shared.contract import TiedTo, ZoneType
from shared.db import create_schema, make_engine
from worker.settings import load_settings, parse_polygon


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rads.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO cameras (camera_id, name, rtsp_url, enabled, target_fps) VALUES
            (1, 'Camera 1', 'rtsp://a', 1, 5), (2, 'Camera 2', 'rtsp://b', 0, 5);
        INSERT INTO zones (zone_id, camera_id, name, type, polygon, active) VALUES
            (1, 1, 'Prep', 'prep', '[[0,0],[100,0],[100,50],[0,50]]', 1),
            (2, 1, 'Bin', 'waste', '[[10,10],[20,10],[20,20]]', 1),
            (3, 1, 'Old', 'prep', '[[0,0],[1,0],[1,1]]', 0),
            (4, 1, 'Broken', 'ignore', 'not json', 1),
            (5, 2, 'Prep 2', 'prep', '[[0,0],[9,0],[9,9]]', 1);
        INSERT INTO hazards VALUES
            ('H-01', 'Head cover missing', 'head_uncovered', 'head_covered', 'worker'),
            ('H-04', 'Phone in preparation area', 'phone', NULL, 'zone');
        INSERT INTO rules (rule_id, hazard_id, camera_id, enabled, min_ratio) VALUES
            (1, 'H-01', 1, 1, 0.7), (2, 'H-04', 1, 1, 0.3), (3, 'H-01', 2, 0, 0.7);
        INSERT INTO rule_zones VALUES (1, 1), (2, 1), (2, 2), (3, 5);
    """)
    con.commit()
    con.close()
    engine = make_engine(path)
    yield engine
    engine.dispose()


def test_load_settings(db):
    s = load_settings(db)
    assert set(s.cameras) == {1, 2}
    assert s.cameras[1].rtsp_url == "rtsp://a" and s.cameras[2].enabled is False
    assert s.zones[1].polygon == ((0, 0), (100, 0), (100, 50), (0, 50))
    assert s.zones[2].type == ZoneType.WASTE
    assert 4 not in s.zones                                  # bad JSON skipped
    assert [z.zone_id for z in s.zones_for(1)] == [1, 2]     # inactive zone 3 left out
    assert s.hazards["H-04"].compliant_class is None and s.hazards["H-04"].tied_to == TiedTo.ZONE
    assert s.rules[2].zone_ids == (1, 2) and s.rules[2].min_ratio == 0.3
    assert s.rules[1].conf_threshold == 0.45 and s.rules[1].window_s == 5.0  # schema defaults
    assert [r.rule_id for r in s.rules_for(1)] == [1, 2]
    assert s.rules_for(2) == []                              # disabled rule


def test_reload_sees_changes(db, tmp_path):
    with db.begin() as con:
        con.exec_driver_sql("UPDATE rules SET conf_threshold = 0.6 WHERE rule_id = 1")
    assert load_settings(db).rules[1].conf_threshold == 0.6


def test_parse_polygon():
    assert parse_polygon("[[1, 2], [3.5, 4]]") == ((1.0, 2.0), (3.5, 4.0))
    for bad in ('{"a": 1}', "[[1, 2, 3]]", "[1, 2]"):
        with pytest.raises(ValueError):
            parse_polygon(bad)


def test_reload_picks_up_a_changed_zone_polygon(db):
    assert load_settings(db).zones[1].polygon == ((0, 0), (100, 0), (100, 50), (0, 50))
    with db.begin() as con:
        con.exec_driver_sql("UPDATE zones SET polygon = '[[0,0],[2688,0],[2688,1520],[0,1520]]' WHERE zone_id = 1")
    assert load_settings(db).zones[1].polygon == ((0, 0), (2688, 0), (2688, 1520), (0, 1520))
