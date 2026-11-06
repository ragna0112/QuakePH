from functools import lru_cache
from urllib.parse import urlsplit
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    database_url: str = "postgresql+psycopg://localhost/quakeph"
    database_pool_mode: Literal["queue", "null"] = "queue"
    database_connect_timeout: int = Field(default=3, ge=1, le=30)
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

    frontend_origins: list[str] = Field(default_factory=lambda: ['http://127.0.0.1:5174', 'http://localhost:5174'])
    api_worker_fresh_seconds: int = Field(default=30, ge=1, le=86400)

    @field_validator('database_url')
    @classmethod
    def normalize_database_url(cls, value):
        # Neon supplies libpq-style URLs; this project installs psycopg 3 only.
        for prefix in ('postgres://', 'postgresql://'):
            if value.startswith(prefix):
                return 'postgresql+psycopg://' + value[len(prefix):]
        return value

    @field_validator('frontend_origins')
    @classmethod
    def validate_origins(cls, values):
        origins = []
        for value in values:
            try:
                parts = urlsplit(value)
                if (parts.scheme not in ('http', 'https') or not parts.hostname
                        or parts.username or parts.password or parts.query or parts.fragment
                        or parts.path not in ('', '/')):
                    raise ValueError('Expected an explicit HTTP(S) origin without credentials or a path')
                parts.port  # Validate port syntax.
            except ValueError as exc:
                raise ValueError('Invalid frontend origin; use scheme://host:port') from exc
            origins.append(value.rstrip('/'))
        return origins

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min_latitude >= self.max_latitude or self.min_longitude >= self.max_longitude:
            raise ValueError("Bounding box minimums must be less than maximums")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
