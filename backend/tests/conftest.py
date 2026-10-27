from uuid import uuid4
import pytest
from sqlalchemy import text
from app.database import get_engine, Base
from app import models  # noqa: F401


def pytest_addoption(parser):
    parser.addoption('--postgres', action='store_true', help='Run integration tests in disposable PostgreSQL schemas')


@pytest.fixture
def pg_engine(request):
    if not request.config.getoption('--postgres'):
        pytest.skip('requires --postgres and the configured PostgreSQL server')
    engine = get_engine()
    schema = 'quakeph_test_' + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(isolated)
        yield isolated
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
