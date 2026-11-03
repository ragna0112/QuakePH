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


@pytest.fixture
def source_run(pg_engine):
    from app.models import pipeline_runs
    from app.ingestion import utcnow
    identifier = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(pipeline_runs.insert().values(id=identifier, ingestion_type='fixture',
            started_at=utcnow(), status='running', source='fixture', scope={}, errors=[]))
    return identifier
