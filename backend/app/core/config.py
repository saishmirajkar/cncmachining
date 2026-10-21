"""Application settings, loaded exclusively from environment variables (prefix ``CNC_``).

No secret has a usable default outside development: ``CNC_SECRET_KEY`` must be set when
``CNC_ENV=production`` or the application refuses to start.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CNC_", env_file=".env", extra="ignore")

    env: str = Field("development", description="development | test | production")
    database_url: str = "postgresql+psycopg://cnc:cnc@localhost:5432/cncplatform"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-only-insecure-secret-change-me"
    access_token_minutes: int = 12 * 60

    storage_dir: Path = BACKEND_DIR / "var" / "storage"
    temp_dir: Path = BACKEND_DIR / "var" / "tmp"

    # queue backend: "rq" (Redis Queue, production), "thread" (dev without worker), "inline" (tests)
    queue_backend: str = "thread"

    max_upload_mb: int = 100
    geometry_timeout_s: int = 180
    cam_timeout_s: int = 600
    cam_memory_limit_mb: int = 4096

    # FreeCAD CAM runtime: a Python interpreter that can ``import FreeCAD`` (see DEPENDENCY_REPORT.md)
    freecad_python: str = "/opt/freecad-env/bin/python"
    freecad_lib_dir: str = "/opt/freecad-env/lib"

    rate_limit_auth_per_min: int = 20
    rate_limit_upload_per_min: int = 30

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    simulation_max_cells: int = 300_000

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.env == "production" and self.secret_key.startswith("dev-only"):
            raise ValueError("CNC_SECRET_KEY must be set in production")
        return self


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    s.temp_dir.mkdir(parents=True, exist_ok=True)
    return s
