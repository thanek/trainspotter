from app.estimator import build_timeline
from app.service import _call_index
from app.static_db import StaticStop

DAY_START = 1_000_000.0

TRIP_CALLING_AT_A_TWICE = [
    StaticStop(0, "A", "Alpha", 52.0, 16.0, 0, 0, 0.0),
    StaticStop(1, "B", "Bravo", 52.0, 16.1, 600, 660, 10.0),
    StaticStop(2, "A", "Alpha", 52.0, 16.0, 1200, 1200, 20.0),
]


def test_call_index_tells_repeated_calls_apart_by_scheduled_time():
    tl = build_timeline(TRIP_CALLING_AT_A_TWICE, DAY_START, None)
    assert _call_index(tl, "A", DAY_START) == 0
    assert _call_index(tl, "A", DAY_START + 1200) == 2


def test_call_index_is_none_for_a_stop_missing_from_the_timeline():
    tl = build_timeline(TRIP_CALLING_AT_A_TWICE, DAY_START, None)
    assert _call_index(tl, "Z", DAY_START) is None
