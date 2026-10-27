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

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min_latitude >= self.max_latitude or self.min_longitude >= self.max_longitude:
            raise ValueError("Bounding box minimums must be less than maximums")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
