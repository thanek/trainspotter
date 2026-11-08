from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .config import settings

log = logging.getLogger(__name__)
_FRACTIONAL_SECONDS = re.compile(r"\.\d+")


def parse_ts(raw: str | None) -> float | None:
    if not raw:
        return None
    return datetime.fromisoformat(_FRACTIONAL_SECONDS.sub("", raw, count=1)).timestamp()


@dataclass(slots=True)
class LiveStop:
    seq: int
    arr: float | None
    dep: float | None
    confirmed: bool
    cancelled: bool
    stop_id: str | None = None


@dataclass(slots=True)
class LiveTrip:
    trip_id: str
    service_date: str
    agency_id: str | None
    numbers: list[str]
    cancelled: bool
    detour: bool
    stops: list[LiveStop]
    by_seq: dict[int, LiveStop] = field(default_factory=dict)


@dataclass(slots=True)
class LiveSnapshot:
    feed_ts: float | None
    by_trip: dict[tuple[str, str], LiveTrip]
    by_number: dict[tuple[str | None, str, str], LiveTrip]

    def match(self, trip_id: str, service_date: str, agency_id: str | None, number: str | None) -> LiveTrip | None:
        hit = self.by_trip.get((trip_id, service_date))
        if hit is None and number:
            # trip_id is unstable upstream; feed author recommends number fallback.
            hit = self.by_number.get((agency_id, number, service_date))
        return hit


def _parse_stop(raw: dict) -> LiveStop:
    return LiveStop(
        seq=raw["stop_sequence"],
        arr=parse_ts(raw.get("arrival")),
        dep=parse_ts(raw.get("departure")),
        confirmed=bool(raw.get("confirmed")),
        cancelled=bool(raw.get("cancelled")),
        stop_id=raw.get("stop_id"),
    )


def _parse_trip(raw: dict) -> LiveTrip:
    stops = [_parse_stop(st) for st in raw.get("stop_times") or []]
    return LiveTrip(
        trip_id=raw["trip_id"],
        service_date=raw["start_date"].replace("-", ""),
        agency_id=raw.get("agency_id"),
        numbers=raw.get("numbers") or [],
        cancelled=bool(raw.get("cancelled")),
        detour=bool(raw.get("detour")),
        stops=stops,
        by_seq={s.seq: s for s in stops},
    )


def parse_feed(raw: bytes) -> LiveSnapshot:
    data = json.loads(raw)
    by_trip: dict[tuple[str, str], LiveTrip] = {}
    by_number: dict[tuple[str | None, str, str], LiveTrip] = {}
    for trip in map(_parse_trip, data.get("trip_updates") or []):
        by_trip[(trip.trip_id, trip.service_date)] = trip
        for number in trip.numbers:
            by_number.setdefault((trip.agency_id, number, trip.service_date), trip)
    return LiveSnapshot(parse_ts(data.get("timestamp")), by_trip, by_number)


class PlkLiveFeed:
    source_id = "plk-opendata-mkuran"

    def __init__(self, url: str | None = None):
        self.url = url or settings.live_feed_url
        self._client: httpx.AsyncClient | None = None
        self._fetch_lock = asyncio.Lock()
        self.snapshot: LiveSnapshot | None = None
        self._etag: str | None = None
        self._last_attempt_at = 0.0
        self.last_error: str | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": settings.user_agent}, follow_redirects=True, timeout=30
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _attempted_recently(self) -> bool:
        return time.time() - self._last_attempt_at < settings.live_feed_cache_ttl_s

    async def _download(self) -> None:
        headers = {"If-None-Match": self._etag} if self._etag and self.snapshot else {}
        r = await self._http().get(self.url, headers=headers)
        if r.status_code == httpx.codes.NOT_MODIFIED:
            return
        r.raise_for_status()
        self.snapshot = await asyncio.to_thread(parse_feed, r.content)
        self._etag = r.headers.get("etag")

    async def get(self) -> LiveSnapshot | None:
        if self._attempted_recently():
            return self.snapshot
        async with self._fetch_lock:
            if self._attempted_recently():
                return self.snapshot
            try:
                await self._download()
                self.last_error = None
            except Exception as e:  # noqa: BLE001 – keep last snapshot
                log.warning("Nie udało się pobrać danych live: %s", e)
                self.last_error = f"{type(e).__name__}: {e}"
            # Also on failure: TTL doubles as backoff.
            self._last_attempt_at = time.time()
            return self.snapshot

    def usable(self, snapshot: LiveSnapshot | None, now: float) -> bool:
        return bool(snapshot and snapshot.feed_ts and now - snapshot.feed_ts <= settings.live_feed_max_age_s)

    def status(self, now: float) -> str:
        if self.snapshot is None:
            return "error" if self.last_error else "idle"
        if not self.usable(self.snapshot, now):
            return "stale"
        return "error" if self.last_error else "ok"
