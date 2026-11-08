from __future__ import annotations

import contextlib
import csv
import io
import logging
import math
import os
import sqlite3
import threading
import time
import zipfile
from array import array
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

import httpx

from .config import settings
from .geo import BBox, Polyline, haversine_km

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE agencies(agency_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE routes(route_id TEXT PRIMARY KEY, agency_id TEXT, short_name TEXT, long_name TEXT, route_type INTEGER);
CREATE TABLE stops(stop_id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, parent_station TEXT);
CREATE TABLE stations(station_id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, trip_count INTEGER);
CREATE TABLE trips(
  trip_idx INTEGER PRIMARY KEY, trip_id TEXT UNIQUE, route_id TEXT, service_id TEXT, shape_id TEXT,
  short_name TEXT, headsign TEXT, category TEXT, number TEXT, name TEXT, first_s INTEGER, last_s INTEGER);
CREATE TABLE service_dates(date TEXT, service_id TEXT, PRIMARY KEY(date, service_id)) WITHOUT ROWID;
CREATE TABLE stop_times(
  trip_idx INTEGER, seq INTEGER, stop_id TEXT, arr_s INTEGER, dep_s INTEGER, dist REAL,
  PRIMARY KEY(trip_idx, seq)) WITHOUT ROWID;
CREATE TABLE shapes(shape_id TEXT PRIMARY KEY, data BLOB);
CREATE VIRTUAL TABLE trip_rtree USING rtree(trip_idx, min_lon, max_lon, min_lat, max_lat);
"""

SCHEMA_VERSION = "2"

STATIONS_FROM_RAIL_STOP_TIMES = """
INSERT INTO stations
SELECT served.station_id, s.name, s.lat, s.lon, COUNT(DISTINCT served.trip_idx)
FROM (
  SELECT COALESCE(NULLIF(p.parent_station, ''), p.stop_id) AS station_id, st.trip_idx
  FROM stop_times st
  JOIN stops p ON p.stop_id = st.stop_id
  JOIN trips t ON t.trip_idx = st.trip_idx
  JOIN routes r ON r.route_id = t.route_id
  WHERE r.route_type != ?
) served
JOIN stops s ON s.stop_id = served.station_id
GROUP BY served.station_id
"""

TRIP_SELECT = """
SELECT t.*, r.agency_id, r.route_type, r.short_name AS route_short_name, a.name AS agency_name
FROM trips t
JOIN routes r ON r.route_id = t.route_id
LEFT JOIN agencies a ON a.agency_id = r.agency_id
"""

BUS_ROUTE_TYPE = 3
SHAPE_OVERHANG_PAD_DEG = 0.02
SQL_IN_CHUNK = 500
SHAPE_CACHE_LIMIT = 2000


class NotReady(Exception):
    pass


class StaticStop(NamedTuple):
    seq: int
    stop_id: str
    name: str
    lat: float
    lon: float
    arr_s: int
    dep_s: int
    dist: float | None


@dataclass(slots=True)
class _TripExtent:
    first_departure_s: float = math.inf
    last_arrival_s: float = -math.inf
    min_lon: float = 180.0
    max_lon: float = -180.0
    min_lat: float = 90.0
    max_lat: float = -90.0

    def include_time(self, arr_s: int, dep_s: int) -> None:
        self.first_departure_s = min(self.first_departure_s, dep_s)
        self.last_arrival_s = max(self.last_arrival_s, arr_s)

    def include_point(self, lat: float, lon: float) -> None:
        self.min_lon, self.max_lon = min(self.min_lon, lon), max(self.max_lon, lon)
        self.min_lat, self.max_lat = min(self.min_lat, lat), max(self.max_lat, lat)

    @property
    def has_area(self) -> bool:
        return self.min_lon <= self.max_lon


def parse_gtfs_time(raw: str) -> int:
    h, m, s = raw.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _parse_gtfs_date(raw: str) -> date:
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    if name not in zf.namelist():
        return
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def _expand_calendar(zf: zipfile.ZipFile) -> set[tuple[str, str]]:
    weekday_columns = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    dates: set[tuple[str, str]] = set()
    for r in _rows(zf, "calendar.txt"):
        runs_on_weekday = [r[c] == "1" for c in weekday_columns]
        day, end = _parse_gtfs_date(r["start_date"]), _parse_gtfs_date(r["end_date"])
        while day <= end:
            if runs_on_weekday[day.weekday()]:
                dates.add((day.strftime("%Y%m%d"), r["service_id"]))
            day += timedelta(days=1)
    for r in _rows(zf, "calendar_dates.txt"):
        key = (r["date"], r["service_id"])
        if r["exception_type"] == "1":
            dates.add(key)
        else:
            dates.discard(key)
    return dates


def _with_computed_distances(points: list[tuple[int, float, float, float | None]]):
    total_km, prev, out = 0.0, None, []
    for seq, lat, lon, _ in points:
        if prev:
            total_km += haversine_km(prev[0], prev[1], lat, lon)
        out.append((seq, lat, lon, total_km))
        prev = (lat, lon)
    return out


def _import_reference_tables(con: sqlite3.Connection, zf: zipfile.ZipFile) -> dict[str, tuple[float, float]]:
    con.executemany(
        "INSERT INTO agencies VALUES(?,?)",
        ((r["agency_id"], r["agency_name"]) for r in _rows(zf, "agency.txt")),
    )
    con.executemany(
        "INSERT INTO routes VALUES(?,?,?,?,?)",
        (
            (r["route_id"], r.get("agency_id"), r.get("route_short_name"), r.get("route_long_name"),
             int(r["route_type"]))
            for r in _rows(zf, "routes.txt")
        ),
    )
    coords: dict[str, tuple[float, float]] = {}
    for r in _rows(zf, "stops.txt"):
        if r.get("stop_lat"):
            coords[r["stop_id"]] = (float(r["stop_lat"]), float(r["stop_lon"]))
            con.execute("INSERT INTO stops VALUES(?,?,?,?,?)",
                        (r["stop_id"], r["stop_name"], *coords[r["stop_id"]], r.get("parent_station") or None))
    con.executemany("INSERT INTO service_dates VALUES(?,?)", sorted(_expand_calendar(zf)))
    return coords


def _import_trips(con: sqlite3.Connection, zf: zipfile.ZipFile) -> dict[str, int]:
    trip_idx: dict[str, int] = {}
    rows = []
    for i, r in enumerate(_rows(zf, "trips.txt")):
        trip_idx[r["trip_id"]] = i
        rows.append((
            i, r["trip_id"], r["route_id"], r["service_id"], r.get("shape_id") or None,
            r.get("trip_short_name") or None, r.get("trip_headsign") or None,
            r.get("plk_category_code") or None, r.get("plk_train_number") or r.get("trip_short_name") or None,
            r.get("plk_train_name") or None,
        ))
    con.executemany("INSERT INTO trips VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL)", rows)
    return trip_idx


def _import_stop_times(con: sqlite3.Connection, zf: zipfile.ZipFile, trip_idx: dict[str, int],
                       coords: dict[str, tuple[float, float]]) -> None:
    extents: dict[int, _TripExtent] = {}

    def rows():
        for r in _rows(zf, "stop_times.txt"):
            ti = trip_idx.get(r["trip_id"])
            arr_raw = r.get("arrival_time") or r.get("departure_time")
            dep_raw = r.get("departure_time") or r.get("arrival_time")
            if ti is None or not arr_raw:
                continue
            arr, dep = parse_gtfs_time(arr_raw), parse_gtfs_time(dep_raw)
            extent = extents.setdefault(ti, _TripExtent())
            extent.include_time(arr, dep)
            if r["stop_id"] in coords:
                extent.include_point(*coords[r["stop_id"]])
            dist = r.get("shape_dist_traveled")
            yield ti, int(r["stop_sequence"]), r["stop_id"], arr, dep, float(dist) if dist else None

    con.executemany("INSERT INTO stop_times VALUES(?,?,?,?,?,?)", rows())
    con.executemany(
        "UPDATE trips SET first_s=?, last_s=? WHERE trip_idx=?",
        ((int(e.first_departure_s), int(e.last_arrival_s), ti) for ti, e in extents.items()),
    )
    pad = SHAPE_OVERHANG_PAD_DEG
    con.executemany(
        "INSERT INTO trip_rtree VALUES(?,?,?,?,?)",
        (
            (ti, e.min_lon - pad, e.max_lon + pad, e.min_lat - pad, e.max_lat + pad)
            for ti, e in extents.items()
            if e.has_area
        ),
    )


def _import_shapes(con: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
    shapes: dict[str, list[tuple[int, float, float, float | None]]] = {}
    for r in _rows(zf, "shapes.txt"):
        dist = r.get("shape_dist_traveled")
        shapes.setdefault(r["shape_id"], []).append((
            int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"]),
            float(dist) if dist else None,
        ))

    def rows():
        for shape_id, points in shapes.items():
            points.sort()
            if any(p[3] is None for p in points):
                points = _with_computed_distances(points)
            packed = array("d")
            for _, lat, lon, km in points:
                packed.extend((lat, lon, km))
            yield shape_id, packed.tobytes()

    con.executemany("INSERT INTO shapes VALUES(?,?)", rows())


def build_database(zip_path: Path, db_path: Path) -> None:
    started = time.monotonic()
    building = db_path.with_suffix(".building")
    building.unlink(missing_ok=True)
    con = sqlite3.connect(building)
    try:
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        with zipfile.ZipFile(zip_path) as zf:
            coords = _import_reference_tables(con, zf)
            trip_idx = _import_trips(con, zf)
            _import_stop_times(con, zf, trip_idx, coords)
            _import_shapes(con, zf)
            con.execute(STATIONS_FROM_RAIL_STOP_TIMES, (BUS_ROUTE_TYPE,))
            feed_version = next((r.get("feed_version") for r in _rows(zf, "feed_info.txt")), None)
        con.executemany(
            "INSERT INTO meta VALUES(?,?)",
            [("feed_version", feed_version or ""), ("imported_at", str(time.time())),
             ("schema_version", SCHEMA_VERSION)],
        )
        con.commit()
    finally:
        con.close()
    os.replace(building, db_path)
    log.info("Zaimportowano rozkład do %s w %.1f s", db_path, time.monotonic() - started)


def download(url: str, dest: Path) -> None:
    partial = dest.with_suffix(".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120,
                      headers={"User-Agent": settings.user_agent}) as r:
        r.raise_for_status()
        with open(partial, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    os.replace(partial, dest)


def _in_chunks(items: Iterable, size: int = SQL_IN_CHUNK) -> Iterator[list]:
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _placeholders(chunk: list) -> str:
    return ",".join("?" * len(chunk))


class StaticData:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._import_lock = threading.Lock()
        self._importing = False
        self.last_error: str | None = None
        self._shape_cache: dict[str, Polyline | None] = {}
        self._shape_cache_db_mtime: float | None = None
        self._meta_cache: dict[str, str] = {}
        self._meta_cache_db_mtime: float | None = None

    @property
    def ready(self) -> bool:
        return self.db_path.exists()

    @property
    def importing(self) -> bool:
        return self._importing

    def age_s(self) -> float | None:
        return time.time() - self.db_path.stat().st_mtime if self.ready else None

    def schema_is_current(self) -> bool:
        return self.meta().get("schema_version") == SCHEMA_VERSION

    def ensure_fresh(self) -> None:
        age = self.age_s()
        if age is not None and age < settings.static_max_age_s and self.schema_is_current():
            return
        with self._import_lock:
            if self._importing:
                return
            self._importing = True
        threading.Thread(target=self._import_in_background, name="gtfs-import", daemon=True).start()

    def _import_in_background(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            zip_path = self.db_path.parent / "polish_trains.zip"
            log.info("Pobieram rozkład z %s", settings.gtfs_url)
            download(settings.gtfs_url, zip_path)
            build_database(zip_path, self.db_path)
            self.last_error = None
        except Exception as e:  # noqa: BLE001 – reported via /sources; old DB stays
            log.exception("Import rozkładu nie powiódł się")
            self.last_error = f"{type(e).__name__}: {e}"
        finally:
            self._importing = False

    def _drop_shape_cache_if_db_replaced(self) -> None:
        mtime = self.db_path.stat().st_mtime
        if mtime != self._shape_cache_db_mtime:
            self._shape_cache.clear()
            self._shape_cache_db_mtime = mtime

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.ready:
            raise NotReady()
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            self._drop_shape_cache_if_db_replaced()
            yield con
        finally:
            con.close()

    def meta(self) -> dict[str, str]:
        if not self.ready:
            return {}
        mtime = self.db_path.stat().st_mtime
        if self._meta_cache_db_mtime != mtime:
            with self.connect() as con:
                self._meta_cache = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
            self._meta_cache_db_mtime = mtime
        return self._meta_cache

    @staticmethod
    def trips_possibly_running_in(con: sqlite3.Connection, bbox: BBox, service_date: str, now_s: float,
                                  include_bus: bool) -> list[sqlite3.Row]:
        return con.execute(
            TRIP_SELECT + """
            JOIN trip_rtree rt ON rt.trip_idx = t.trip_idx
            JOIN service_dates sd ON sd.date = :d AND sd.service_id = t.service_id
            WHERE rt.min_lon <= :max_lon AND rt.max_lon >= :min_lon
              AND rt.min_lat <= :max_lat AND rt.max_lat >= :min_lat
              AND t.first_s <= :now_s AND t.last_s + :max_delay >= :now_s
              AND (:include_bus OR r.route_type != :bus)
            """,
            {
                "d": service_date, "now_s": now_s, "max_delay": settings.max_assumed_delay_s,
                "include_bus": include_bus, "bus": BUS_ROUTE_TYPE,
                "min_lon": bbox.min_lon, "max_lon": bbox.max_lon, "min_lat": bbox.min_lat, "max_lat": bbox.max_lat,
            },
        ).fetchall()

    @staticmethod
    def trip(con: sqlite3.Connection, trip_id: str) -> sqlite3.Row | None:
        return con.execute(TRIP_SELECT + " WHERE t.trip_id = ?", (trip_id,)).fetchone()

    @staticmethod
    def trains_on(con: sqlite3.Connection, service_date: str) -> list[sqlite3.Row]:
        return con.execute(
            TRIP_SELECT + """
            JOIN service_dates sd ON sd.date = ? AND sd.service_id = t.service_id
            WHERE r.route_type != ?
            """,
            (service_date, BUS_ROUTE_TYPE),
        ).fetchall()

    @staticmethod
    def stations(con: sqlite3.Connection) -> list[sqlite3.Row]:
        return con.execute("SELECT * FROM stations").fetchall()

    @staticmethod
    def runs_on(con: sqlite3.Connection, service_id: str, service_date: str) -> bool:
        return con.execute(
            "SELECT 1 FROM service_dates WHERE date=? AND service_id=?", (service_date, service_id)
        ).fetchone() is not None

    @staticmethod
    def stop_times_for(con: sqlite3.Connection, trip_idxs: Iterable[int]) -> dict[int, list[StaticStop]]:
        out: dict[int, list[StaticStop]] = {}
        for chunk in _in_chunks(trip_idxs):
            rows = con.execute(
                f"""
                SELECT st.trip_idx, st.seq, st.stop_id, s.name, s.lat, s.lon, st.arr_s, st.dep_s, st.dist
                FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.trip_idx IN ({_placeholders(chunk)})
                ORDER BY st.trip_idx, st.seq
                """,
                chunk,
            )
            for trip_idx, *stop in rows:
                out.setdefault(trip_idx, []).append(StaticStop(*stop))
        return out

    @staticmethod
    def stops_by_id(con: sqlite3.Connection, stop_ids: Iterable[str]) -> dict[str, sqlite3.Row]:
        out: dict[str, sqlite3.Row] = {}
        for chunk in _in_chunks(set(stop_ids)):
            for r in con.execute(f"SELECT * FROM stops WHERE stop_id IN ({_placeholders(chunk)})", chunk):
                out[r["stop_id"]] = r
        return out

    def shape(self, con: sqlite3.Connection, shape_id: str | None) -> Polyline | None:
        if not shape_id:
            return None
        if shape_id not in self._shape_cache:
            if len(self._shape_cache) > SHAPE_CACHE_LIMIT:
                self._shape_cache.clear()
            row = con.execute("SELECT data FROM shapes WHERE shape_id=?", (shape_id,)).fetchone()
            self._shape_cache[shape_id] = Polyline.from_blob(row[0]) if row else None
        return self._shape_cache[shape_id]
