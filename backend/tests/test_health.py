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
