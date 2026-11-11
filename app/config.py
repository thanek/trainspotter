from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"TS_{name}", default)


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _default_frontend_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "frontend")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    frontend_dir: Path = field(default_factory=lambda: Path(_env("FRONTEND_DIR", _default_frontend_dir())))
    app_variant: str = field(default_factory=lambda: _env("APP_VARIANT", "prod"))

    gtfs_url: str = field(default_factory=lambda: _env("GTFS_URL", "https://mkuran.pl/gtfs/polish_trains.zip"))
    static_max_age_s: float = field(default_factory=lambda: _env_float("STATIC_MAX_AGE_S", 24 * 3600))

    live_feed_url: str = field(
        default_factory=lambda: _env("LIVE_FEED_URL", "https://mkuran.pl/gtfs/polish_trains/updates.json")
    )
    live_feed_cache_ttl_s: float = field(default_factory=lambda: _env_float("LIVE_FEED_CACHE_TTL_S", 30))
    live_feed_max_age_s: float = field(default_factory=lambda: _env_float("LIVE_FEED_MAX_AGE_S", 900))

    gps_fix_max_age_s: float = field(default_factory=lambda: _env_float("GPS_FIX_MAX_AGE_S", 180))
    gps_timeout_s: float = field(default_factory=lambda: _env_float("GPS_TIMEOUT_S", 5))

    overview_mode_min_diag_km: float = field(default_factory=lambda: _env_float("OVERVIEW_MODE_MIN_DIAG_KM", 150))
    max_features: int = field(default_factory=lambda: int(_env("MAX_FEATURES", "3000")))
    max_assumed_delay_s: int = field(default_factory=lambda: int(_env("MAX_ASSUMED_DELAY_S", str(4 * 3600))))

    timezone: str = "Europe/Warsaw"
    user_agent: str = "trainspotter/0.1"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gtfs.sqlite"


settings = Settings()
