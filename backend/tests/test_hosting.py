"""Hosting contracts without contacting Neon or modifying application rows."""
import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app import database, hosted
from app.config import Settings


@pytest.mark.parametrize('prefix', ['postgres://', 'postgresql://', 'postgresql+psycopg://'])
def test_neon_url_uses_installed_psycopg_driver(prefix):
    settings = Settings(_env_file=None, database_url=prefix+'user:password@host/quakeph?sslmode=require')
    assert settings.database_url == 'postgresql+psycopg://user:password@host/quakeph?sslmode=require'


@pytest.mark.parametrize('url', [
    'postgresql://u:p@localhost/quakeph?sslmode=require',
    'postgresql://u:p@ep-test-pooler.us-east-2.aws.neon.tech/quakeph?sslmode=require',
    'postgresql://u:p@ep-test.us-east-2.aws.neon.tech/otherproject?sslmode=require',
    'postgresql://u:p@ep-test.us-east-2.aws.neon.tech/quakeph',
])
def test_hosted_writes_reject_wrong_target_or_pooler(url):
    with pytest.raises(ValueError):
        hosted.validate_target(Settings(_env_file=None, database_url=url))


def test_hosted_direct_target():
    hosted.validate_target(Settings(_env_file=None, database_url=
        'postgresql://u:p@ep-test.us-east-2.aws.neon.tech/quakeph?sslmode=require'))


def test_serverless_database_options_and_utc_transactions(pg_engine, monkeypatch):
    settings = Settings(_env_file=None, database_url=pg_engine.url.render_as_string(hide_password=False),
                        database_pool_mode='null', database_connect_timeout=10)
    monkeypatch.setattr(database, 'get_settings', lambda: settings)
    database.get_engine.cache_clear()
    engine = database.get_engine()
    try:
        assert isinstance(engine.pool, NullPool)
        with engine.connect() as connection:
            assert connection.execute(text('SHOW TimeZone')).scalar_one() == 'UTC'
            assert connection.execute(text('SHOW statement_timeout')).scalar_one() == '3s'
            assert connection.connection.dbapi_connection.prepare_threshold is None
    finally:
        engine.dispose()
        database.get_engine.cache_clear()


def test_cloud_failures_do_not_print_credentials(monkeypatch, capsys):
    def fail():
        raise RuntimeError('postgresql://secret-user:super-secret@private-host/quakeph')
    monkeypatch.setattr(hosted, 'get_settings', fail)
    assert hosted.run('recent') == 1
    output = capsys.readouterr().out
    assert 'super-secret' not in output and 'private-host' not in output
    assert json.loads(output)['reason'] == 'RuntimeError'


def test_migration_is_separate_from_ingestion(monkeypatch):
    monkeypatch.setattr(hosted, 'get_settings', lambda: Mock())
    monkeypatch.setattr(hosted, 'validate_target', lambda settings: None)
    monkeypatch.setattr(hosted, 'get_engine', Mock(return_value=Mock()))
    monkeypatch.setattr(hosted, 'session_lock', lambda *args: nullcontext())
    upgrade, check, ingest = Mock(), Mock(), Mock()
    monkeypatch.setattr(hosted.command, 'upgrade', upgrade)
    monkeypatch.setattr(hosted.command, 'check', check)
    monkeypatch.setattr(hosted, 'ingest', ingest)
    assert hosted.run('migrate') == 0
    upgrade.assert_called_once()
    check.assert_called_once()
    ingest.assert_not_called()


def test_service_routing_preserves_api_and_homepage():
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root/'vercel.json').read_text())
    assert config['services']['frontend']['outputDirectory'] == 'dist'
    assert config['services']['frontend']['root'] == 'frontend'
    assert config['services']['backend']['root'] == 'backend'
    assert config['services']['backend']['entrypoint'] == 'app.main:app'
    routes = config['rewrites']
    assert routes[0] == {'source': '/api', 'destination': {'service': 'backend'}}
    assert routes[1] == {'source': '/api/:path*', 'destination': {'service': 'backend'}}
    assert routes[-1] == {'source': '/:path*', 'destination': {'service': 'frontend'}}
    assert all('path' not in route['destination'] for route in routes)
