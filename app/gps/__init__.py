from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..geo import BBox


@dataclass(slots=True)
class GpsFix:
    source: str
    lat: float
    lon: float
    observed_at: float
    bearing: float | None = None
    speed_kmh: float | None = None
    trip_id: str | None = None
    service_date: str | None = None
    agency_id: str | None = None
    number: str | None = None
    label: str | None = None


class GpsSource(ABC):
    source_id: str
    coverage: BBox

    @abstractmethod
    async def fetch(self, bbox: BBox) -> list[GpsFix]: ...

    async def aclose(self) -> None:
        return None


SOURCES: list[GpsSource] = []
