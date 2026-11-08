from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import service
from .config import settings
from .geo import BBox
from .gps import SOURCES
from .models import SourceInfo, TrainCollection
from .static_db import NotReady

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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


@app.get("/api/v1/sources", response_model=list[SourceInfo])
async def sources():
    return service.sources_status()


if settings.frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
