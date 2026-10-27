import pytest
from pydantic import ValidationError
from app.config import Settings


def test_bounding_box_is_configurable(monkeypatch):
    monkeypatch.setenv("MIN_LATITUDE", "5")
    assert Settings(_env_file=None).min_latitude == 5


@pytest.mark.parametrize("bounds", [
    {"min_latitude": 23, "max_latitude": 22},
    {"min_longitude": 130, "max_longitude": 116},
    {"min_latitude": -91},
])
def test_invalid_bounds_rejected(bounds):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **bounds)
