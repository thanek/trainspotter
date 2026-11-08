from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EstimationMethod = Literal["shape_interpolation", "linear_interpolation", "at_stop"]
EstimateBasis = Literal["confirmed", "forecast", "schedule"]
TrainStatus = Literal["moving", "at_stop", "held", "unknown"]


class EstimateInfo(BaseModel):
    method: EstimationMethod
    fromStop: str
    toStop: str
    progress: float = Field(description="0–1, postęp na odcinku fromStop → toStop")
    basedOn: EstimateBasis = Field(
        description="confirmed – ostatnie zdarzenie potwierdzone przez PLK; forecast – prognoza PLK; "
        "schedule – sam rozkład (brak danych live)"
    )


class PositionInfo(BaseModel):
    accuracy: Literal["gps", "estimated"]
    source: str = Field(description="Identyfikator źródła pozycji, patrz /api/v1/sources")
    observedAt: str | None = Field(
        description="Czas pomiaru GPS albo ostatniego potwierdzonego zdarzenia PLK (ISO 8601)"
    )
    estimate: EstimateInfo | None = None


class TrainProperties(BaseModel):
    trainId: str
    tripId: str | None = None
    serviceDate: str | None = None
    number: str | None = None
    name: str | None = None
    carrier: str | None = None
    carrierId: str | None = None
    category: str | None = None
    origin: str | None = None
    destination: str | None = None
    delayMinutes: int | None = None
    bearing: int | None = None
    speedKmh: float | None = None
    status: TrainStatus = Field(
        description="held – czekamy na potwierdzenie PLK przejazdu przez następną stację"
    )
    position: PositionInfo


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class TrainFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: TrainProperties


class SourceInfo(BaseModel):
    id: str
    kind: Literal["schedule", "live", "gps"]
    status: str
    updatedAt: str | None = None
    detail: str | None = None


class SearchResult(BaseModel):
    trainId: str
    serviceDate: str
    number: str | None = None
    name: str | None = None
    carrier: str | None = None
    carrierId: str | None = None
    category: str | None = None
    origin: str
    destination: str
    departure: str = Field(description="Odjazd ze stacji początkowej (rzeczywisty lub prognozowany, gdy znany)")
    arrival: str
    state: Literal["running", "not_started", "finished", "cancelled"]
    train: TrainFeature | None = Field(None, description="Bieżąca pozycja; tylko dla state=running")


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


class TrainCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    generatedAt: str
    mode: Literal["detailed", "overview"]
    truncated: bool
    sources: list[SourceInfo]
    features: list[TrainFeature]
