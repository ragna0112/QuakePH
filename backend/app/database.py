from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3, "options": "-c timezone=UTC -c statement_timeout=3000"},
    )


def check_database() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
