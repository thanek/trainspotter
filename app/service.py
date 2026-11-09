from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .config import settings
from .estimator import Estimate, StopPoint, build_detour_timeline, build_timeline, estimate, stop_progress
from .geo import BBox, Polyline
from .gps import SOURCES, GpsFix, GpsSource
from .realtime import LiveSnapshot, LiveTrip, PlkLiveFeed
from .search import SearchQuery, expand_slash_numbers
from .static_db import NotReady, StaticData

log = logging.getLogger(__name__)

TZ = ZoneInfo(settings.timezone)
SCHEDULE_SOURCE_ID = "gtfs-schedule"
NEARBY_PREFILTER_PAD_DEG = 0.05

static = StaticData(settings.db_path)
live = PlkLiveFeed()
_gps_status: dict[str, dict] = {}


def gtfs_service_day_start(d: date) -> float:
    # GTFS: noon minus 12h, not midnight (matters on DST days).
    return datetime(d.year, d.month, d.day, 12, tzinfo=TZ).timestamp() - 12 * 3600


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds") if ts is not None else None


def make_train_id(service_date: str, trip_id: str) -> str:
    return f"{service_date}:{trip_id}"


def parse_train_id(train_id: str) -> tuple[str, date, str]:
    service_date, trip_id = train_id.split(":", 1)
    return service_date, date(int(service_date[:4]), int(service_date[4:6]), int(service_date[6:8])), trip_id


def _today_and_yesterday(now: float) -> list[date]:
    today = datetime.fromtimestamp(now, TZ).date()
    return [today, today - timedelta(days=1)]


def _dashed(service_date: str) -> str:
    return f"{service_date[:4]}-{service_date[4:6]}-{service_date[6:]}"


def _point_feature(lat: float, lon: float, props: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": props}


def _estimated_properties(trip: sqlite3.Row, service_date: str, tl: list[StopPoint], est: Estimate) -> dict:
    return {
        "trainId": make_train_id(service_date, trip["trip_id"]),
        "tripId": trip["trip_id"],
        "serviceDate": _dashed(service_date),
        "number": trip["number"],
        "name": trip["name"],
        "carrier": trip["agency_name"],
        "carrierId": trip["agency_id"],
        "category": trip["category"] or trip["route_short_name"],
        "origin": tl[0].name,
        "destination": trip["headsign"] or tl[-1].name,
        "delayMinutes": round(est.delay_s / 60) if est.delay_s is not None else None,
        "bearing": round(est.bearing) if est.bearing is not None else None,
        "speedKmh": None,
        "status": est.status,
        "position": {
            "accuracy": "estimated",
            "source": SCHEDULE_SOURCE_ID if est.based_on == "schedule" else live.source_id,
            "observedAt": iso(est.observed_at),
            "estimate": {
                "method": est.method,
                "fromStop": tl[est.from_idx].name,
                "toStop": tl[est.to_idx].name,
                "progress": round(est.progress, 3),
                "basedOn": est.based_on,
            },
        },
    }


@dataclass(slots=True)
class _Run:
    trip: sqlite3.Row
    service_date: str
    service_day_start: float
    live: LiveTrip | None

    @property
    def train_id(self) -> str:
        return make_train_id(self.service_date, self.trip["trip_id"])

    @property
    def on_detour(self) -> bool:
        return self.live is not None and self.live.detour


def _timeline(con: sqlite3.Connection, run: _Run, stops_by_trip: dict) -> list[StopPoint]:
    if run.on_detour:
        lookup = static.stops_by_id(con, [s.stop_id for s in run.live.stops if s.stop_id])
        return build_detour_timeline(run.live, lookup)
    return build_timeline(stops_by_trip.get(run.trip["trip_idx"], []), run.service_day_start, run.live)


def _shape_for(con: sqlite3.Connection, run: _Run) -> Polyline | None:
    return None if run.on_detour else static.shape(con, run.trip["shape_id"])


def _runs_in(con: sqlite3.Connection, bbox: BBox, now: float, snapshot: LiveSnapshot | None,
             include_bus: bool) -> list[_Run]:
    runs: dict[str, _Run] = {}
    for d in _today_and_yesterday(now):
        day_start = gtfs_service_day_start(d)
        service_date = d.strftime("%Y%m%d")
        for trip in static.trips_possibly_running_in(con, bbox, service_date, now - day_start, include_bus):
            live_trip = snapshot.match(trip["trip_id"], service_date, trip["agency_id"], trip["number"]) \
                if snapshot else None
            run = _Run(trip, service_date, day_start, live_trip)
            if not (live_trip and live_trip.cancelled):
                runs.setdefault(run.train_id, run)
    return list(runs.values())


def compute_estimates(bbox: BBox, now: float, snapshot: LiveSnapshot | None, overview: bool,
                      include_bus: bool) -> list[dict]:
    nearby = bbox.padded(NEARBY_PREFILTER_PAD_DEG)
    features = []
    with static.connect() as con:
        runs = _runs_in(con, bbox, now, snapshot, include_bus)
        stops_by_trip = static.stop_times_for(con, {r.trip["trip_idx"] for r in runs})
        for run in runs:
            tl = _timeline(con, run, stops_by_trip)
            has_live = run.live is not None
            rough = estimate(tl, now, has_live)
            if rough is None or not nearby.contains(rough.lat, rough.lon):
                continue
            shape = None if overview else _shape_for(con, run)
            est = estimate(tl, now, has_live, shape) if shape else rough
            if bbox.contains(est.lat, est.lon):
                features.append(_point_feature(est.lat, est.lon, _estimated_properties(run.trip, run.service_date,
                                                                                       tl, est)))
    return features


async def _fetch_from(src: GpsSource, bbox: BBox) -> list[GpsFix]:
    try:
        fixes = await asyncio.wait_for(src.fetch(bbox), settings.gps_timeout_s)
        _gps_status[src.source_id] = {"status": "ok", "updatedAt": time.time(), "detail": None}
        return fixes
    except Exception as e:  # noqa: BLE001 – fall back to estimate
        log.warning("Źródło GPS %s: %s", src.source_id, e)
        previous = _gps_status.get(src.source_id, {})
        _gps_status[src.source_id] = {"status": "error", "updatedAt": previous.get("updatedAt"),
                                      "detail": f"{type(e).__name__}: {e}"}
        return []


async def fetch_gps(bbox: BBox) -> list[GpsFix]:
    covering = [s for s in SOURCES if s.coverage.intersects(bbox)]
    results = await asyncio.gather(*(_fetch_from(s, bbox) for s in covering))
    return [fix for fixes in results for fix in fixes]


class _FeatureIndex:
    def __init__(self, features: list[dict]):
        self.by_trip: dict[tuple, dict] = {}
        self.by_number: dict[tuple, dict] = {}
        self.by_bare_number: dict[str, list[dict]] = {}
        for f in features:
            p = f["properties"]
            service_date = (p.get("serviceDate") or "").replace("-", "")
            self.by_trip[(p.get("tripId"), service_date)] = f
            if p.get("number"):
                self.by_number[(p.get("carrierId"), p["number"], service_date)] = f
                self.by_bare_number.setdefault(p["number"], []).append(f)

    def find(self, fix: GpsFix) -> dict | None:
        if fix.trip_id and fix.service_date and (hit := self.by_trip.get((fix.trip_id, fix.service_date))):
            return hit
        if fix.number and fix.service_date and (hit := self.by_number.get((fix.agency_id, fix.number,
                                                                            fix.service_date))):
            return hit
        same_number = self.by_bare_number.get(fix.number or "", [])
        return same_number[0] if len(same_number) == 1 else None


def _gps_position(fix: GpsFix) -> dict:
    return {"accuracy": "gps", "source": fix.source, "observedAt": iso(fix.observed_at), "estimate": None}


def _apply_fix(feature: dict, fix: GpsFix) -> None:
    feature["geometry"]["coordinates"] = [round(fix.lon, 6), round(fix.lat, 6)]
    p = feature["properties"]
    p["position"] = _gps_position(fix)
    p["speedKmh"] = fix.speed_kmh
    if fix.bearing is not None:
        p["bearing"] = round(fix.bearing)


def _unmatched_gps_feature(fix: GpsFix) -> dict:
    return _point_feature(fix.lat, fix.lon, {
        "trainId": f"gps:{fix.source}:{fix.label or fix.number or f'{fix.lat:.5f},{fix.lon:.5f}'}",
        "number": fix.number,
        "carrierId": fix.agency_id,
        "bearing": round(fix.bearing) if fix.bearing is not None else None,
        "speedKmh": fix.speed_kmh,
        "status": "unknown",
        "position": _gps_position(fix),
    })


def merge_gps(features: list[dict], fixes: list[GpsFix], now: float, bbox: BBox) -> list[dict]:
    index = _FeatureIndex(features)
    unmatched = []
    for fix in fixes:
        if now - fix.observed_at > settings.gps_fix_max_age_s:
            continue
        if (target := index.find(fix)) is not None:
            _apply_fix(target, fix)
        elif bbox.contains(fix.lat, fix.lon):
            unmatched.append(_unmatched_gps_feature(fix))
    return features + unmatched


def _schedule_status() -> dict:
    meta = static.meta()
    if static.importing:
        status = "importing"
    elif static.last_error:
        status = "error"
    else:
        status = "ok" if static.ready else "missing"
    version = meta.get("feed_version")
    return {
        "id": SCHEDULE_SOURCE_ID, "kind": "schedule", "status": status,
        "updatedAt": iso(float(meta["imported_at"])) if meta.get("imported_at") else None,
        "detail": static.last_error or (f"feed_version={version}" if version else None),
    }


def sources_status() -> list[dict]:
    now = time.time()
    snapshot = live.snapshot
    statuses = [
        _schedule_status(),
        {"id": live.source_id, "kind": "live", "status": live.status(now),
         "updatedAt": iso(snapshot.feed_ts) if snapshot else None, "detail": live.last_error},
    ]
    for s in SOURCES:
        g = _gps_status.get(s.source_id, {})
        statuses.append({"id": s.source_id, "kind": "gps", "status": g.get("status", "idle"),
                         "updatedAt": iso(g.get("updatedAt")), "detail": g.get("detail")})
    return statuses


async def _usable_snapshot(now: float) -> LiveSnapshot | None:
    snapshot = await live.get()
    return snapshot if live.usable(snapshot, now) else None


def _require_static() -> None:
    static.ensure_fresh()
    if not static.ready:
        raise NotReady()


async def trains_in_bbox(bbox: BBox, now: float | None = None, include_bus: bool = False) -> dict:
    _require_static()
    now = now or time.time()
    overview = bbox.diag_km > settings.overview_mode_min_diag_km

    gps_task = None if overview else asyncio.create_task(fetch_gps(bbox))
    snapshot = await _usable_snapshot(now)
    features = await asyncio.to_thread(compute_estimates, bbox, now, snapshot, overview, include_bus)
    if gps_task is not None:
        features = merge_gps(features, await gps_task, now, bbox)

    return {
        "type": "FeatureCollection",
        "generatedAt": iso(now),
        "mode": "overview" if overview else "detailed",
        "truncated": len(features) > settings.max_features,
        "sources": sources_status(),
        "features": features[: settings.max_features],
    }


def _delay_minutes(actual: float, scheduled: float | None) -> int | None:
    return round((actual - scheduled) / 60) if scheduled is not None else None


def _time_basis(stop: StopPoint, has_live: bool) -> str:
    if stop.confirmed:
        return "confirmed"
    return "forecast" if has_live else "schedule"


def _station_ids(con: sqlite3.Connection, tl: list[StopPoint]) -> dict[str, str]:
    rows = static.stops_by_id(con, [s.stop_id for s in tl])
    return {stop_id: r["parent_station"] or stop_id for stop_id, r in rows.items()}


def _stop_view(s: StopPoint, progress: str, has_live: bool, station_id: str | None) -> dict:
    return {
        "stopId": s.stop_id, "stationId": station_id, "name": s.name, "coordinates": [s.lon, s.lat],
        "scheduledArrival": iso(s.scheduled_arr), "scheduledDeparture": iso(s.scheduled_dep),
        "arrival": iso(s.arr), "departure": iso(s.dep), "confirmed": s.confirmed,
        "arrivalDelayMinutes": _delay_minutes(s.arr, s.scheduled_arr),
        "departureDelayMinutes": _delay_minutes(s.dep, s.scheduled_dep),
        "timeBasis": _time_basis(s, has_live),
        "progress": progress,
    }


def _detail(train_id: str, now: float, snapshot: LiveSnapshot | None) -> dict | None:
    service_date, d, trip_id = parse_train_id(train_id)
    with static.connect() as con:
        trip = static.trip(con, trip_id)
        if trip is None or not static.runs_on(con, trip["service_id"], service_date):
            return None
        live_trip = snapshot.match(trip_id, service_date, trip["agency_id"], trip["number"]) if snapshot else None
        run = _Run(trip, service_date, gtfs_service_day_start(d), live_trip)
        tl = _timeline(con, run, static.stop_times_for(con, [trip["trip_idx"]]))
        shape = _shape_for(con, run)
        cancelled = bool(live_trip and live_trip.cancelled)
        est = None if cancelled else estimate(tl, now, live_trip is not None, shape)
        route = shape.coordinates() if shape is not None else [[round(s.lon, 6), round(s.lat, 6)] for s in tl]
        state = _run_state(tl, est, now, cancelled)
        progress = stop_progress(len(tl), est, finished=state == "finished")
        station_ids = _station_ids(con, tl)
        return {
            "trainId": train_id,
            "cancelled": cancelled,
            "detour": run.on_detour,
            "current": _point_feature(est.lat, est.lon, _estimated_properties(trip, service_date, tl, est))
            if est else None,
            "summary": _search_result(run, tl, est, state) if len(tl) >= 2 else None,
            "stops": [_stop_view(s, p, live_trip is not None, station_ids.get(s.stop_id))
                      for s, p in zip(tl, progress)],
            "route": {"type": "LineString", "coordinates": route},
        }


async def train_detail(train_id: str, now: float | None = None) -> dict | None:
    _require_static()
    now = now or time.time()
    try:
        parse_train_id(train_id)
    except ValueError:
        return None
    return await asyncio.to_thread(_detail, train_id, now, await _usable_snapshot(now))


SEARCH_RESULT_LIMIT = 30
SEARCH_STATE_ORDER = {"running": 0, "not_started": 1, "finished": 2, "cancelled": 3}


def _matching_runs(con: sqlite3.Connection, dates: list[date], snapshot: LiveSnapshot | None,
                   predicate: Callable[[sqlite3.Row], bool]) -> list[_Run]:
    runs = []
    for d in dates:
        service_date = d.strftime("%Y%m%d")
        day_start = gtfs_service_day_start(d)
        for trip in static.trains_on(con, service_date):
            if predicate(trip):
                live_trip = snapshot.match(trip["trip_id"], service_date, trip["agency_id"], trip["number"]) \
                    if snapshot else None
                runs.append(_Run(trip, service_date, day_start, live_trip))
    return runs


def _run_state(tl: list[StopPoint], est: Estimate | None, now: float, cancelled: bool) -> str:
    if cancelled:
        return "cancelled"
    if est is not None:
        return "running"
    return "not_started" if now < tl[0].dep else "finished"


def _search_result(run: _Run, tl: list[StopPoint], est: Estimate | None, state: str) -> dict:
    trip = run.trip
    return {
        "trainId": run.train_id,
        "serviceDate": _dashed(run.service_date),
        "number": trip["number"],
        "name": trip["name"],
        "carrier": trip["agency_name"],
        "carrierId": trip["agency_id"],
        "category": trip["category"] or trip["route_short_name"],
        "origin": tl[0].name,
        "destination": trip["headsign"] or tl[-1].name,
        "departure": iso(tl[0].dep),
        "arrival": iso(tl[-1].arr),
        "state": state,
        "train": _point_feature(est.lat, est.lon, _estimated_properties(trip, run.service_date, tl, est))
        if est else None,
    }


def _evaluate(con: sqlite3.Connection, runs: list[_Run], now: float) -> list[tuple[_Run, dict]]:
    stops_by_trip = static.stop_times_for(con, {r.trip["trip_idx"] for r in runs})
    evaluated = []
    for run in runs:
        tl = _timeline(con, run, stops_by_trip)
        if len(tl) < 2:
            continue
        cancelled = bool(run.live and run.live.cancelled)
        est = None if cancelled else estimate(tl, now, run.live is not None, _shape_for(con, run))
        evaluated.append((run, _search_result(run, tl, est, _run_state(tl, est, now, cancelled))))
    return evaluated


def _by_relevance(result: dict) -> tuple:
    return SEARCH_STATE_ORDER[result["state"]], result["departure"]


STATION_RESULT_LIMIT = 10


def _station_info(station: sqlite3.Row) -> dict:
    return {
        "stationId": station["station_id"],
        "name": station["name"],
        "coordinates": [round(station["lon"], 6), round(station["lat"], 6)],
        "tripCount": station["trip_count"],
    }


def _station_result(station: sqlite3.Row, query: SearchQuery) -> dict:
    return {**_station_info(station), "exactMatch": query.is_exact_name(station["name"])}


def _station(station_id: str) -> dict | None:
    with static.connect() as con:
        station = static.station(con, station_id) if static.schema_is_current() else None
        return _station_info(station) if station else None


async def station_detail(station_id: str) -> dict | None:
    _require_static()
    return await asyncio.to_thread(_station, station_id)


def _search_stations(con: sqlite3.Connection, query: SearchQuery) -> list[dict]:
    if not static.schema_is_current():
        return []
    matching = [s for s in static.stations(con) if query.matches_name(s["name"])]
    matching.sort(key=lambda s: (*query.name_rank(s["name"]), -s["trip_count"]))
    return [_station_result(s, query) for s in matching[:STATION_RESULT_LIMIT]]


def _search_trains(con: sqlite3.Connection, query: SearchQuery, now: float,
                   snapshot: LiveSnapshot | None) -> list[dict]:
    today = datetime.fromtimestamp(now, TZ).strftime("%Y%m%d")

    def matches(trip: sqlite3.Row) -> bool:
        return query.matches_train(trip["name"], trip["number"], trip["category"], trip["agency_id"])

    runs = _matching_runs(con, _today_and_yesterday(now), snapshot, matches)
    results = [r for run, r in _evaluate(con, runs, now) if r["state"] == "running" or run.service_date == today]
    return sorted(results, key=_by_relevance)[:SEARCH_RESULT_LIMIT]


def _search(query: SearchQuery, now: float, snapshot: LiveSnapshot | None) -> dict:
    with static.connect() as con:
        return {"stations": _search_stations(con, query), "results": _search_trains(con, query, now, snapshot)}


def _follow(carrier_id: str, number: str, service_day: date | None, now: float,
            snapshot: LiveSnapshot | None) -> dict | None:
    def is_followed_train(trip: sqlite3.Row) -> bool:
        return trip["agency_id"] == carrier_id and number in expand_slash_numbers(trip["number"])

    dates = [service_day] if service_day else _today_and_yesterday(now)
    with static.connect() as con:
        results = [r for _, r in _evaluate(con, _matching_runs(con, dates, snapshot, is_followed_train), now)]
    return min(results, key=_by_relevance, default=None)


async def follow_train(carrier_id: str, number: str, service_day: date | None = None,
                       now: float | None = None) -> dict | None:
    _require_static()
    now = now or time.time()
    return await asyncio.to_thread(_follow, carrier_id, number, service_day, now, await _usable_snapshot(now))


async def search(raw_query: str, now: float | None = None) -> dict:
    _require_static()
    now = now or time.time()
    query = SearchQuery.parse(raw_query)
    if not query.tokens:
        return {"stations": [], "results": []}
    return await asyncio.to_thread(_search, query, now, await _usable_snapshot(now))
