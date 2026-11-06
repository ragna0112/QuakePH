from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from app.main import app

client = TestClient(app)


def test_live_does_not_access_database():
    with patch("app.main.check_database", side_effect=AssertionError("must not query")):
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_queries_database():
    with patch("app.main.check_database") as probe:
        response = client.get("/health/ready")
    probe.assert_called_once_with()
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_ready_failure_returns_503_without_secrets():
    with patch("app.main.check_database", side_effect=OperationalError("secret-url", {}, Exception("password"))):
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}


def test_public_config_excludes_credentials():
    payload = client.get("/api/config").json()
    assert set(payload) == {"coverage", "bounds", "display_timezone"}
    assert payload["coverage"] == "Philippines and surrounding region"
    assert payload["display_timezone"] == "Asia/Manila"


def test_query_failure_logs_only_safe_fields(caplog):
    import json
    from app.database import log_database_failure
    original = Exception('postgresql://private-user:private-password@private-host/db?secret=value')
    original.sqlstate = '42P01'
    with caplog.at_level('ERROR', logger='quakeph.database'):
        log_database_failure(OperationalError('private SQL', {'secret': 'value'}, original), 'dashboard_query')
    payload = json.loads(caplog.records[-1].message)
    assert payload['category'] == 'missing_table'
    assert payload['sqlstate'] == '42P01'
    assert 'private' not in caplog.text and 'secret' not in caplog.text


def test_readiness_failure_is_logged_safely(caplog):
    with patch('app.main.check_database', side_effect=OperationalError('private SQL', {}, Exception('private-password'))):
        response = client.get('/health/ready')
    assert response.status_code == 503
    assert '"operation": "readiness"' in caplog.text
    assert 'private' not in caplog.text


def test_untrusted_sqlstate_is_omitted(caplog):
    from app.database import log_database_failure
    original = Exception('secret')
    original.sqlstate = 'private-host'
    log_database_failure(OperationalError('secret', {}, original), 'secret-operation')
    assert 'private-host' not in caplog.text and 'secret' not in caplog.text


def test_dashboard_handler_logs_query_failure(caplog):
    from app.api import read_connection
    def unavailable():
        raise OperationalError('private SQL', {}, Exception('private-password'))
        yield
    app.dependency_overrides[read_connection] = unavailable
    try:
        response = client.get('/api/earthquakes')
    finally:
        app.dependency_overrides.pop(read_connection, None)
    assert response.status_code == 503
    assert '"operation": "dashboard_query"' in caplog.text
    assert 'private' not in caplog.text
