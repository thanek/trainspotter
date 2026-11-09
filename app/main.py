from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import service
from .config import settings
from .geo import BBox
from .gps import SOURCES
from .models import SearchResponse, SearchResult, SourceInfo, StationInfo, TrainCollection
from .static_db import NotReady

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
mimetypes.add_type("application/manifest+json", ".webmanifest")


@asynccontextmanager
async def lifespan(_: FastAPI):
    service.static.ensure_fresh()
    yield
    await service.live.aclose()
    for source in SOURCES:
        await source.aclose()


app = FastAPI(title="Trainspotter API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(NotReady)
async def not_ready(_, __):
    return JSONResponse({"detail": "Rozkład jest importowany, spróbuj za chwilę."}, status_code=503,
                        headers={"Retry-After": "10"})


@app.get("/api/v1/health")
async def health():
    return {"status": "ok" if service.static.ready else "starting", "importing": service.static.importing}


@app.get("/api/v1/trains", response_model=TrainCollection)
async def trains(
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat (WGS84)", examples=["16.70,52.31,17.15,52.50"]),
    at: datetime | None = Query(None, description="Chwila (ISO 8601); domyślnie teraz"),
    include_bus: bool = Query(False, description="Uwzględnij zastępczą komunikację autobusową"),
):
    try:
        box = BBox.parse(bbox)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await service.trains_in_bbox(box, at.timestamp() if at else None, include_bus)


@app.get("/api/v1/trains/{train_id}")
async def train(train_id: str, at: datetime | None = None):
    detail = await service.train_detail(train_id, at.timestamp() if at else None)
    if detail is None:
        raise HTTPException(404, "Nie znaleziono kursu")
    return detail


@app.get("/api/v1/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=2, max_length=60,
                   description="Nazwa lub numer pociągu albo nazwa stacji, np. „szkuner”, „IC 3822”, „Poznań Główny”"),
):
    return {"query": q, **await service.search(q)}


@app.get("/api/v1/follow", response_model=SearchResult)
async def follow(
    carrier: str = Query(..., pattern=r"^[A-Za-z0-9]{1,8}$", description="Identyfikator przewoźnika, np. IC"),
    number: str = Query(..., pattern=r"^[0-9]{1,6}$", description="Numer pociągu, np. 8312"),
    service_date: date | None = Query(None, alias="date", description="Data kursu; bez niej – bieżący kurs"),
):
    result = await service.follow_train(carrier, number, service_date)
    if result is None:
        raise HTTPException(404, "Nie znaleziono pociągu")
    return result


@app.get("/api/v1/stations/{station_id}", response_model=StationInfo)
async def station(station_id: str):
    detail = await service.station_detail(station_id)
    if detail is None:
        raise HTTPException(404, "Nie znaleziono stacji")
    return detail


@app.get("/api/v1/stations/{station_id}/departures")
async def station_departures(station_id: str, limit: int = Query(8, ge=1, le=30)):
    board = await service.station_board(station_id, limit)
    if board is None:
        raise HTTPException(404, "Nie znaleziono stacji")
    return board


@app.get("/api/v1/sources", response_model=list[SourceInfo])
async def sources():
    return service.sources_status()


if settings.frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
