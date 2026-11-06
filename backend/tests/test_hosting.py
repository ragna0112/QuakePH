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
    assert routes[-2] == {'source': '/', 'destination': {'service': 'frontend', 'path': '/index.html'}}
    assert all('path' not in route['destination'] for route in routes if route['source'] != '/')


def test_hosted_import_checks_migration_head_before_source_request(monkeypatch, capsys):
    monkeypatch.setattr(hosted, 'get_settings', lambda: Mock())
    monkeypatch.setattr(hosted, 'validate_target', lambda settings: None)
    engine = Mock()
    engine.connect.return_value = nullcontext(Mock())
    monkeypatch.setattr(hosted, 'get_engine', lambda: engine)
    monkeypatch.setattr(hosted, 'session_lock', lambda *args: nullcontext())
    monkeypatch.setattr(hosted.MigrationContext, 'configure', lambda connection: Mock(get_current_heads=lambda: ('old',)))
    monkeypatch.setattr(hosted.ScriptDirectory, 'from_config', lambda config: Mock(get_heads=lambda: ('new',)))
    ingestion = Mock()
    monkeypatch.setattr(hosted, 'ingest', ingestion)
    assert hosted.run('recent') == 1
    ingestion.assert_not_called()
    assert json.loads(capsys.readouterr().out)['reason'] == 'RuntimeError'


def test_hosted_partial_import_exits_nonzero_and_omits_raw_errors(monkeypatch, capsys):
    monkeypatch.setattr(hosted, 'get_settings', lambda: Mock())
    monkeypatch.setattr(hosted, 'validate_target', lambda settings: None)
    engine = Mock()
    engine.connect.return_value = nullcontext(Mock())
    monkeypatch.setattr(hosted, 'get_engine', lambda: engine)
    monkeypatch.setattr(hosted, 'session_lock', lambda *args: nullcontext())
    monkeypatch.setattr(hosted.MigrationContext, 'configure', lambda connection: Mock(get_current_heads=lambda: ('head',)))
    monkeypatch.setattr(hosted.ScriptDirectory, 'from_config', lambda config: Mock(get_heads=lambda: ('head',)))
    monkeypatch.setattr(hosted.httpx, 'Client', lambda **kwargs: nullcontext(Mock()))
    monkeypatch.setattr(hosted, 'ingest', lambda *args: {'status': 'partial', 'rejected': 1, 'errors': ['secret']})
    assert hosted.run('recent') == 1
    output = capsys.readouterr().out
    assert 'secret' not in output
    assert json.loads(output)['rejected'] == 1


@pytest.mark.parametrize('url,checks', [
    ('', ['database_url_present']),
    ('not a connection URL', ['database_url_parseable']),
    ('postgresql://u:p@localhost/quakeph?sslmode=require', ['neon_hostname']),
    ('postgresql://u:p@ep-test-pooler.aws.neon.tech/quakeph?sslmode=require', ['direct_connection']),
    ('postgresql://u:p@ep-test.aws.neon.tech/neondb?sslmode=require', ['database_name_quakeph']),
    ('postgresql://u:p@ep-test.aws.neon.tech/quakeph', ['tls_sslmode']),
])
def test_safe_target_diagnostics_without_database_access(monkeypatch, capsys, url, checks):
    monkeypatch.setattr(hosted, 'get_settings', lambda: Settings(_env_file=None, database_url=url))
    engine = Mock()
    monkeypatch.setattr(hosted, 'get_engine', engine)
    assert hosted.run('check') == 1
    result = json.loads(capsys.readouterr().out)
    assert result['stage'] == 'target_validation'
    assert result['failed_checks'] == checks
    assert 'neon.tech' not in json.dumps(result)
    engine.assert_not_called()


def test_check_success_cannot_connect_or_migrate(monkeypatch, capsys):
    monkeypatch.setattr(hosted, 'get_settings', lambda: Settings(_env_file=None, database_url=
        'postgresql://u:private-password@ep-test.aws.neon.tech/quakeph?sslmode=require'))
    engine, upgrade = Mock(), Mock()
    monkeypatch.setattr(hosted, 'get_engine', engine)
    monkeypatch.setattr(hosted.command, 'upgrade', upgrade)
    assert hosted.run('check') == 0
    assert 'private-password' not in capsys.readouterr().out
    engine.assert_not_called()
    upgrade.assert_not_called()


@pytest.mark.parametrize('method,stage', [('upgrade', 'migration_upgrade'), ('check', 'schema_check')])
def test_migration_failure_stage_is_safe(monkeypatch, capsys, method, stage):
    monkeypatch.setattr(hosted, 'get_settings', lambda: Mock())
    monkeypatch.setattr(hosted, 'validate_target', lambda settings: None)
    monkeypatch.setattr(hosted, 'get_engine', Mock(return_value=Mock()))
    monkeypatch.setattr(hosted, 'session_lock', lambda *args: nullcontext())
    monkeypatch.setattr(hosted.command, 'upgrade', Mock())
    monkeypatch.setattr(hosted.command, 'check', Mock())
    getattr(hosted.command, method).side_effect = ValueError('private-password and private-host')
    assert hosted.run('migrate') == 1
    output = capsys.readouterr().out
    assert json.loads(output)['stage'] == stage
    assert 'private-password' not in output and 'private-host' not in output
