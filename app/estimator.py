from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from .geo import Polyline, bearing_deg, lerp
from .models import EstimateBasis, EstimationMethod
from .realtime import LiveTrip
from .static_db import StaticStop

PLK_REPORTING_LAG_TOLERANCE_S = 10 * 60
UNCONFIRMED_TERMINUS_HOLD_S = 15 * 60
UNCONFIRMED_ORIGIN_HOLD_S = 20 * 60

EstimatedStatus = Literal["moving", "at_stop", "held"]


@dataclass(slots=True)
class StopPoint:
    stop_id: str
    name: str
    lat: float
    lon: float
    dist_km: float | None
    arr: float
    dep: float
    scheduled_arr: float | None
    scheduled_dep: float | None
    confirmed: bool = False


@dataclass(slots=True)
class Estimate:
    lat: float
    lon: float
    bearing: float | None
    status: EstimatedStatus
    from_idx: int
    to_idx: int
    progress: float
    based_on: EstimateBasis
    method: EstimationMethod
    delay_s: float | None
    observed_at: float | None


@dataclass(slots=True)
class _Location:
    at_stop: bool
    from_idx: int
    to_idx: int
    progress: float

    @classmethod
    def stop(cls, idx: int) -> "_Location":
        return cls(True, idx, idx, 0.0)


def build_timeline(stops: Sequence[StaticStop], service_day_start: float, live: LiveTrip | None) -> list[StopPoint]:
    out: list[StopPoint] = []
    propagated_delay = 0.0
    for st in stops:
        scheduled_arr, scheduled_dep = service_day_start + st.arr_s, service_day_start + st.dep_s
        dwell = scheduled_dep - scheduled_arr
        update = live.by_seq.get(st.seq) if live else None
        if update is not None and update.cancelled:
            continue
        if update is not None and (update.arr is not None or update.dep is not None):
            arr = update.arr if update.arr is not None else update.dep - dwell
            dep = update.dep if update.dep is not None else update.arr + dwell
            propagated_delay = max(0.0, dep - scheduled_dep)
            confirmed = update.confirmed
        else:
            arr, dep = scheduled_arr + propagated_delay, scheduled_dep + propagated_delay
            confirmed = False
        if out:
            arr = max(arr, out[-1].dep)
        out.append(StopPoint(st.stop_id, st.name, st.lat, st.lon, st.dist,
                             arr, max(arr, dep), scheduled_arr, scheduled_dep, confirmed))
    return out


def build_detour_timeline(live: LiveTrip, stops_by_id: Mapping[str, Mapping]) -> list[StopPoint]:
    out: list[StopPoint] = []
    for update in live.stops:
        stop = stops_by_id.get(update.stop_id) if update.stop_id else None
        if stop is None or update.cancelled or (update.arr is None and update.dep is None):
            continue
        arr = update.arr if update.arr is not None else update.dep
        dep = update.dep if update.dep is not None else update.arr
        if out:
            arr = max(arr, out[-1].dep)
        out.append(StopPoint(stop["stop_id"], stop["name"], stop["lat"], stop["lon"], None,
                             arr, max(arr, dep), None, None, update.confirmed))
    return out


def _locate_by_time(tl: list[StopPoint], now: float) -> _Location:
    for i, stop in enumerate(tl):
        if now <= stop.dep:
            if i == 0 or now >= stop.arr:
                return _Location.stop(i)
            prev = tl[i - 1]
            travel_s = stop.arr - prev.dep
            return _Location(False, i - 1, i, (now - prev.dep) / travel_s if travel_s > 0 else 1.0)
    return _Location.stop(len(tl) - 1)


def _last_confirmed_idx(tl: list[StopPoint]) -> int:
    return max((i for i, s in enumerate(tl) if s.confirmed), default=-1)


def _apply_confirmation_limit(tl: list[StopPoint], loc: _Location, now: float,
                              last_confirmed: int) -> tuple[_Location, bool]:
    first_unconfirmed = min(last_confirmed + 1, len(tl) - 1)
    confirmation_overdue_s = now - tl[first_unconfirmed].arr
    nothing_confirmed_for_long = last_confirmed < 0 and now - tl[0].dep >= UNCONFIRMED_ORIGIN_HOLD_S
    within_reporting_lag = confirmation_overdue_s <= PLK_REPORTING_LAG_TOLERANCE_S
    if nothing_confirmed_for_long or within_reporting_lag or loc.to_idx <= first_unconfirmed:
        return loc, False
    return _Location.stop(first_unconfirmed), True


def _position(tl: list[StopPoint], loc: _Location, shape: Polyline | None):
    a, b = tl[loc.from_idx], tl[loc.to_idx]
    if shape is not None and len(shape) > 1 and a.dist_km is not None and b.dist_km is not None:
        lat, lon, brg = shape.point_at_km(lerp(a.dist_km, b.dist_km, loc.progress))
        return lat, lon, brg, "at_stop" if loc.at_stop else "shape_interpolation"
    if not loc.at_stop:
        lat, lon = lerp(a.lat, b.lat, loc.progress), lerp(a.lon, b.lon, loc.progress)
        return lat, lon, bearing_deg(a.lat, a.lon, b.lat, b.lon), "linear_interpolation"
    following = tl[loc.from_idx + 1] if loc.from_idx + 1 < len(tl) else None
    brg = bearing_deg(a.lat, a.lon, following.lat, following.lon) if following else None
    return a.lat, a.lon, brg, "at_stop"


def _delay_s(tl: list[StopPoint], loc: _Location) -> float | None:
    ref = tl[loc.to_idx]
    if loc.at_stop:
        return ref.dep - ref.scheduled_dep if ref.scheduled_dep is not None else None
    return ref.arr - ref.scheduled_arr if ref.scheduled_arr is not None else None


def _basis(tl: list[StopPoint], loc: _Location, has_live: bool, held: bool, last_confirmed: int) -> EstimateBasis:
    if not has_live:
        return "schedule"
    if held:
        return "confirmed" if last_confirmed >= 0 else "forecast"
    return "confirmed" if tl[loc.from_idx].confirmed else "forecast"


def _observed_at(tl: list[StopPoint], last_confirmed: int, now: float) -> float | None:
    if last_confirmed < 0:
        return None
    stop = tl[last_confirmed]
    return stop.dep if stop.dep <= now else stop.arr


def _is_running(tl: list[StopPoint], now: float, has_live: bool) -> bool:
    terminus = tl[-1]
    hold_at_terminus = UNCONFIRMED_TERMINUS_HOLD_S if has_live and not terminus.confirmed else 0
    return tl[0].dep <= now <= terminus.arr + hold_at_terminus


def estimate(tl: list[StopPoint], now: float, has_live: bool, shape: Polyline | None = None) -> Estimate | None:
    if len(tl) < 2 or not _is_running(tl, now, has_live):
        return None
    loc = _locate_by_time(tl, now)
    last_confirmed = _last_confirmed_idx(tl) if has_live else -1
    held = False
    if has_live:
        loc, held = _apply_confirmation_limit(tl, loc, now, last_confirmed)
    lat, lon, brg, method = _position(tl, loc, shape)
    status: EstimatedStatus = "held" if held else ("at_stop" if loc.at_stop else "moving")
    return Estimate(
        lat=lat, lon=lon, bearing=brg, status=status,
        from_idx=loc.from_idx, to_idx=loc.to_idx, progress=max(0.0, min(1.0, loc.progress)),
        based_on=_basis(tl, loc, has_live, held, last_confirmed), method=method,
        delay_s=_delay_s(tl, loc), observed_at=_observed_at(tl, last_confirmed, now),
    )
