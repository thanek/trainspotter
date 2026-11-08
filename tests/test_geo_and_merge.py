import pytest

from app.geo import BBox, Polyline, bearing_deg
from app.gps import GpsFix
from app.service import merge_gps

NOW = 2_000_000.0
BOX = BBox(16.0, 52.0, 17.0, 53.0)


def test_bbox_parse_rejects_inverted_coordinates():
    with pytest.raises(ValueError):
        BBox.parse("17,52,16,53")


def test_bearing_east_is_90():
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90)


def test_polyline_clamps_beyond_end():
    line = Polyline([0.0, 0.0], [0.0, 1.0], [0.0, 10.0])
    assert line.point_at_km(50)[:2] == (0.0, 1.0)


def feature(number="123", trip_id="T1"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [16.5, 52.5]},
        "properties": {"trainId": f"20261108:{trip_id}", "tripId": trip_id, "serviceDate": "2026-11-08",
                       "number": number, "carrierId": "SKM", "bearing": None, "speedKmh": None,
                       "status": "moving", "position": {"accuracy": "estimated", "source": "x"}},
    }


def test_fresh_gps_fix_replaces_estimate():
    fix = GpsFix("test-gps", 52.6, 16.6, NOW - 10, bearing=45, number="123", agency_id="SKM",
                 service_date="20261108")
    merged = merge_gps([feature()], [fix], NOW, BOX)
    assert merged[0]["geometry"]["coordinates"] == [16.6, 52.6]
    assert merged[0]["properties"]["position"]["accuracy"] == "gps"
    assert merged[0]["properties"]["position"]["source"] == "test-gps"


def test_stale_gps_fix_is_ignored():
    fix = GpsFix("test-gps", 52.6, 16.6, NOW - 3600, trip_id="T1", service_date="20261108")
    merged = merge_gps([feature()], [fix], NOW, BOX)
    assert merged[0]["properties"]["position"]["accuracy"] == "estimated"


def test_unmatched_fix_inside_bbox_becomes_own_feature():
    fix = GpsFix("test-gps", 52.7, 16.7, NOW, number="999", label="veh-1")
    merged = merge_gps([feature()], [fix], NOW, BOX)
    assert len(merged) == 2
    assert merged[1]["properties"]["status"] == "unknown"
