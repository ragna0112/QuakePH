from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    database_url: str = "postgresql+psycopg://localhost/quakeph"
    min_latitude: float = Field(default=4, ge=-90, le=90)
    max_latitude: float = Field(default=22, ge=-90, le=90)
    min_longitude: float = Field(default=116, ge=-180, le=180)
    max_longitude: float = Field(default=130, ge=-180, le=180)

    worker_recent_seconds: float = Field(default=300, ge=1, allow_inf_nan=False)
    worker_reconcile_seconds: float = Field(default=86400, ge=1, allow_inf_nan=False)
    worker_reconcile_days: int = Field(default=30, ge=1, le=3650)
    worker_retry_seconds: float = Field(default=300, ge=1, allow_inf_nan=False)
    worker_heartbeat_seconds: float = Field(default=10, ge=1, allow_inf_nan=False)
    worker_poll_seconds: float = Field(default=1, ge=0.1, allow_inf_nan=False)
    recheck_max_ids: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min_latitude >= self.max_latitude or self.min_longitude >= self.max_longitude:
            raise ValueError("Bounding box minimums must be less than maximums")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
