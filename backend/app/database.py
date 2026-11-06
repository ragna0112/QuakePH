import json
import logging
import re
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


def log_database_failure(exc, operation):
    """Emit fixed categories and validated SQLSTATE only, never driver text/SQL."""
    original = getattr(exc, 'orig', None)
    state = getattr(original, 'sqlstate', None)
    state = state if isinstance(state, str) and re.fullmatch(r'[0-9A-Z]{5}', state) else None
    categories = {'42P01': 'missing_table', '42703': 'missing_column',
                  '42501': 'permission_denied', '25006': 'read_only_violation',
                  '25001': 'transaction_state', '57014': 'query_cancelled',
                  '28P01': 'authentication_failed', '3D000': 'database_missing'}
    category = categories.get(state, 'database_error')
    if state and state.startswith('08'):
        category = 'connection_failure'
    logging.getLogger('quakeph.database').error(json.dumps({
        'event': 'database_failure',
        'operation': operation if operation in ('dashboard_query', 'readiness') else 'database',
        'category': category, 'sqlstate': state,
        'connection_invalidated': bool(getattr(exc, 'connection_invalidated', False)),
    }))
