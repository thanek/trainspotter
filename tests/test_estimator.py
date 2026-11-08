import pytest

from app.estimator import (
    PLK_REPORTING_LAG_TOLERANCE_S,
    UNCONFIRMED_ORIGIN_HOLD_S,
    build_timeline,
    estimate,
    stop_progress,
)
from app.geo import Polyline
from app.realtime import LiveStop, LiveTrip
from app.static_db import StaticStop

DAY_START = 1_000_000.0

STOPS = [
    StaticStop(0, "A", "Alpha", 52.0, 16.0, 0, 0, 0.0),
    StaticStop(1, "B", "Bravo", 52.0, 16.1, 600, 660, 10.0),
    StaticStop(2, "C", "Charlie", 52.0, 16.2, 1200, 1200, 20.0),
]


def live_trip(*stops: LiveStop) -> LiveTrip:
    return LiveTrip("T", "20261108", "XX", ["1"], False, False, list(stops), {s.seq: s for s in stops})


def at(offset_s: float) -> float:
    return DAY_START + offset_s


def test_schedule_only_moving_halfway():
    tl = build_timeline(STOPS, DAY_START, None)
    est = estimate(tl, at(300), has_live=False)
    assert est.status == "moving"
    assert est.based_on == "schedule"
    assert est.progress == pytest.approx(0.5)
    assert est.lon == pytest.approx(16.05)
    assert est.delay_s == 0


def test_schedule_only_dwelling_at_stop():
    est = estimate(build_timeline(STOPS, DAY_START, None), at(630), has_live=False)
    assert est.status == "at_stop"
    assert (est.lat, est.lon) == (52.0, 16.1)


def test_not_running_before_departure_and_after_arrival():
    tl = build_timeline(STOPS, DAY_START, None)
    assert estimate(tl, at(-1), has_live=False) is None
    assert estimate(tl, at(1201), has_live=False) is None


def test_live_delay_is_propagated_to_stops_without_updates():
    live = live_trip(LiveStop(0, None, at(120), True, False))
    tl = build_timeline(STOPS, DAY_START, live)
    assert tl[1].arr == at(720)
    assert tl[2].arr == at(1320)


def test_confirmed_departure_moving_on_forecast():
    live = live_trip(
        LiveStop(0, None, at(120), True, False),
        LiveStop(1, at(720), at(780), False, False),
    )
    est = estimate(build_timeline(STOPS, DAY_START, live), at(420), has_live=True)
    assert est.status == "moving"
    assert est.based_on == "confirmed"
    assert est.delay_s == 120
    assert est.observed_at == at(120)


def test_held_before_first_unconfirmed_stop():
    live = live_trip(
        LiveStop(0, None, at(0), True, False),
        LiveStop(1, at(600), at(660), False, False),
    )
    est = estimate(build_timeline(STOPS, DAY_START, live), at(600 + PLK_REPORTING_LAG_TOLERANCE_S + 60),
                   has_live=True)
    assert est.status == "held"
    assert est.to_idx == 1
    assert (est.lat, est.lon) == (52.0, 16.1)


def test_keeps_moving_on_forecast_within_reporting_lag():
    live = live_trip(
        LiveStop(0, None, at(0), True, False),
        LiveStop(1, at(600), at(660), False, False),
    )
    est = estimate(build_timeline(STOPS, DAY_START, live), at(900), has_live=True)
    assert est.status == "moving"
    assert (est.from_idx, est.to_idx) == (1, 2)
    assert est.based_on == "forecast"


def test_no_confirmations_at_all_stops_holding_after_limit():
    live = live_trip(LiveStop(0, None, at(0), False, False))
    tl = build_timeline(STOPS, DAY_START, live)
    assert estimate(tl, at(PLK_REPORTING_LAG_TOLERANCE_S + 60), has_live=True).status == "held"
    late = estimate(tl, at(UNCONFIRMED_ORIGIN_HOLD_S + 60), has_live=True)
    assert late is None or late.status != "held"


def test_cancelled_stop_is_skipped():
    live = live_trip(LiveStop(1, None, None, False, True))
    tl = build_timeline(STOPS, DAY_START, live)
    assert [s.stop_id for s in tl] == ["A", "C"]


def test_shape_interpolation_uses_distance_along_track():
    shape = Polyline([52.0, 52.1, 52.0], [16.0, 16.05, 16.1], [0.0, 5.0, 10.0])
    est = estimate(build_timeline(STOPS, DAY_START, None), at(300), has_live=False, shape=shape)
    assert est.method == "shape_interpolation"
    assert (est.lat, est.lon) == pytest.approx((52.1, 16.05))


def test_stop_progress_while_moving_marks_departed_stops_as_passed():
    est = estimate(build_timeline(STOPS, DAY_START, None), at(300), has_live=False)
    assert stop_progress(3, est, finished=False) == ["passed", "upcoming", "upcoming"]


def test_stop_progress_at_stop_marks_it_current():
    est = estimate(build_timeline(STOPS, DAY_START, None), at(630), has_live=False)
    assert stop_progress(3, est, finished=False) == ["passed", "current", "upcoming"]


def test_stop_progress_when_held_keeps_awaited_stop_upcoming():
    live = live_trip(
        LiveStop(0, None, at(0), True, False),
        LiveStop(1, at(600), at(660), False, False),
    )
    est = estimate(build_timeline(STOPS, DAY_START, live), at(600 + PLK_REPORTING_LAG_TOLERANCE_S + 60),
                   has_live=True)
    assert stop_progress(3, est, finished=False) == ["passed", "upcoming", "upcoming"]


def test_stop_progress_without_estimate_depends_on_whether_trip_finished():
    assert stop_progress(2, None, finished=True) == ["passed", "passed"]
    assert stop_progress(2, None, finished=False) == ["upcoming", "upcoming"]
