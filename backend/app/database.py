from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    settings = get_settings()
    serverless = settings.database_pool_mode == 'null'
    arguments = {"connect_timeout": settings.database_connect_timeout}
    if serverless:
        # No session startup options or connection-local prepared statements for
        # Neon's transaction pooler. Close client connections after each checkout.
        arguments['prepare_threshold'] = None
    else:
        arguments['options'] = '-c timezone=UTC -c statement_timeout=3000'
    engine = create_engine(settings.database_url, pool_pre_ping=True,
                           connect_args=arguments,
                           **({'poolclass': NullPool} if serverless else {}))
    if serverless:
        @event.listens_for(engine, 'begin')
        def transaction_settings(connection):
            connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '3000ms'")
    return engine



def check_database() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
