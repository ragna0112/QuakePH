from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import check_database

app = FastAPI(title="QuakePH", version="0.1.0")


@app.get("/health/live")
def liveness():
    """Process health deliberately does not depend on PostgreSQL."""
    return {"status": "ok"}


@app.get("/health/ready")
def readiness():
    """Readiness verifies a real database query without exposing connection details."""
    try:
        check_database()
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
    return {"status": "ready", "database": "ok"}


@app.get("/api/config")
def public_config():
    settings = get_settings()
    return {
        "coverage": "Philippines and surrounding region",
        "bounds": {
            "min_latitude": settings.min_latitude,
            "max_latitude": settings.max_latitude,
            "min_longitude": settings.min_longitude,
            "max_longitude": settings.max_longitude,
        },
        "display_timezone": "Asia/Manila",
    }
