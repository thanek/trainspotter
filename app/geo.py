from __future__ import annotations

import math
from array import array
from bisect import bisect_right
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    if lat1 == lat2 and lon1 == lon2:
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass(frozen=True, slots=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, raw: str) -> "BBox":
        parts = [float(x) for x in raw.split(",")]
        if len(parts) != 4:
            raise ValueError("bbox musi mieć postać minLon,minLat,maxLon,maxLat")
        box = cls(*parts)
        if not (-180 <= box.min_lon < box.max_lon <= 180 and -90 <= box.min_lat < box.max_lat <= 90):
            raise ValueError("niepoprawne współrzędne bbox")
        return box

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def intersects(self, other: "BBox") -> bool:
        return not (
            other.min_lon > self.max_lon
            or other.max_lon < self.min_lon
            or other.min_lat > self.max_lat
            or other.max_lat < self.min_lat
        )

    def padded(self, deg: float) -> "BBox":
        return BBox(self.min_lon - deg, self.min_lat - deg, self.max_lon + deg, self.max_lat + deg)

    @property
    def diag_km(self) -> float:
        return haversine_km(self.min_lat, self.min_lon, self.max_lat, self.max_lon)


class Polyline:
    __slots__ = ("lats", "lons", "cumulative_km")

    def __init__(self, lats, lons, cumulative_km):
        self.lats, self.lons, self.cumulative_km = lats, lons, cumulative_km

    @classmethod
    def from_blob(cls, blob: bytes) -> "Polyline":
        lat_lon_km_triples = array("d")
        lat_lon_km_triples.frombytes(blob)
        return cls(lat_lon_km_triples[0::3], lat_lon_km_triples[1::3], lat_lon_km_triples[2::3])

    def __len__(self) -> int:
        return len(self.cumulative_km)

    def _segment_bearing_near(self, i: int) -> float | None:
        for j in (i, i + 1, i - 1):
            if 0 <= j < len(self) - 1:
                brg = bearing_deg(self.lats[j], self.lons[j], self.lats[j + 1], self.lons[j + 1])
                if brg is not None:
                    return brg
        return None

    def point_at_km(self, km: float) -> tuple[float, float, float | None]:
        n = len(self)
        if n == 1:
            return self.lats[0], self.lons[0], None
        i = max(0, min(bisect_right(self.cumulative_km, km) - 1, n - 2))
        km0, km1 = self.cumulative_km[i], self.cumulative_km[i + 1]
        t = 0.0 if km1 <= km0 else max(0.0, min(1.0, (km - km0) / (km1 - km0)))
        lat = lerp(self.lats[i], self.lats[i + 1], t)
        lon = lerp(self.lons[i], self.lons[i + 1], t)
        return lat, lon, self._segment_bearing_near(i)

    def coordinates(self) -> list[list[float]]:
        return [[round(lon, 6), round(lat, 6)] for lat, lon in zip(self.lats, self.lons)]
