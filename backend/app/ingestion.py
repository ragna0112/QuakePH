"""Weekly USGS ingestion. Run from backend: python -m app.ingestion."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import math
import time
from uuid import uuid4

import httpx
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.database import get_engine
from app.models import earthquakes, pipeline_runs

SOURCE = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson'
COVERAGE = 'Philippines and surrounding region'
COUNTERS = ('fetched', 'in_scope', 'inserted', 'updated', 'unchanged', 'rejected',
            'out_of_scope', 'excluded_type', 'failed_request_attempts')


def utcnow():
    return datetime.now(timezone.utc)


def finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field}: expected finite number')
    try:
        if not math.isfinite(value):
            raise ValueError(f'{field}: expected finite number')
    except OverflowError as exc:
        raise ValueError(f'{field}: number too large') from exc
    return value


def timestamp(value, field):
    finite_number(value, field)
    if not isinstance(value, int):
        raise ValueError(f'{field}: expected integer milliseconds')
    try:
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{field}: timestamp out of range') from exc


def optional_text(properties, key):
    value = properties.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f'{key}: expected string or null')
    return value


def normalize(feature):
    if not isinstance(feature, dict) or feature.get('type') != 'Feature':
        raise ValueError('record must be a GeoJSON Feature')
    event_id = feature.get('id')
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError('id: expected nonempty string')
    properties = feature.get('properties')
    geometry = feature.get('geometry')
    if not isinstance(properties, dict):
        raise ValueError('properties: expected object')
    if not isinstance(geometry, dict) or geometry.get('type') != 'Point':
        raise ValueError('geometry: expected Point')
    coords = geometry.get('coordinates')
    if not isinstance(coords, list) or len(coords) != 3:
        raise ValueError('coordinates: expected [longitude, latitude, depth]')
    longitude, latitude, depth = [finite_number(v, k) for v, k in zip(
        coords, ('longitude', 'latitude', 'depth'))]
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError('coordinates: longitude/latitude out of range')
    event_type = properties.get('type')
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError('type: expected nonempty event type')
    magnitude = properties.get('mag')
    if magnitude is not None:
        finite_number(magnitude, 'mag')
    # JSONB cannot represent NaN/Infinity, including in unused source properties.
    try:
        json.dumps(feature, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('raw event: not finite, serializable JSON') from exc
    return dict(
        usgs_id=event_id, event_time=timestamp(properties.get('time'), 'time'),
        source_updated_at=timestamp(properties.get('updated'), 'updated'),
        magnitude=magnitude, magnitude_type=optional_text(properties, 'magType'),
        place=optional_text(properties, 'place'), longitude=longitude, latitude=latitude,
        depth_km=depth, review_status=optional_text(properties, 'status'),
        event_type=event_type, source_url=optional_text(properties, 'url'), raw_event=feature,
    )


def in_bounds(event, settings):
    return (settings.min_latitude <= event['latitude'] <= settings.max_latitude
            and settings.min_longitude <= event['longitude'] <= settings.max_longitude)


def validate_feed(payload, *, require_count=True):
    if not isinstance(payload, dict) or payload.get('type') != 'FeatureCollection':
        raise ValueError('response must be a GeoJSON FeatureCollection')
    features, metadata = payload.get('features'), payload.get('metadata')
    if not isinstance(features, list) or not isinstance(metadata, dict):
        raise ValueError('response requires features list and metadata object')
    count_invalid = ((require_count or 'count' in metadata) and
                     (type(metadata.get('count')) is not int or metadata['count'] != len(features)))
    if count_invalid or metadata.get('status') != 200:
        raise ValueError('response metadata status/count is invalid or inconsistent')
    return features


def retry_delay(header, attempt):
    delay = min(2 ** (attempt - 1), 30)
    if header:
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(header) - utcnow()).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    return max(0, min(delay, 30)) if math.isfinite(delay) else 30


class RequestFailure(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def request_usgs(client, url, record_failure, sleep=time.sleep, params=None):
    """Shared bounded retry policy for recent feeds and catalog requests."""
    for attempt in range(1, 4):
        response = None
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            code = response.status_code if response is not None else None
            record_failure({'kind': 'request', 'attempt': attempt,
                            'http_status': code, 'reason': type(exc).__name__, 'url': url})
            transient = code is None or code in (408, 429, 500, 502, 503, 504)
            if not transient or attempt == 3:
                raise RequestFailure(f'USGS request failed after {attempt} attempt(s); HTTP {code}', code) from exc
            sleep(retry_delay(response.headers.get('Retry-After') if response is not None else None, attempt))
    raise AssertionError('unreachable')


def fetch_feed(client, record_failure, sleep=time.sleep):
    response = request_usgs(client, SOURCE, record_failure, sleep)
    try:
        return validate_feed(response.json())
    except (ValueError, TypeError) as exc:
        raise ValueError(f'invalid USGS response: {exc}') from exc


def upsert_event(connection, event, seen_at):
    values = dict(event, first_seen_at=seen_at, last_seen_at=seen_at)
    # Unique-key conflict handling waits for concurrent inserts. No read-before-write.
    created = connection.execute(insert(earthquakes).values(**values).on_conflict_do_nothing(
        index_elements=[earthquakes.c.usgs_id]).returning(earthquakes.c.usgs_id)).scalar_one_or_none()
    if created is not None:
        return 'inserted'
    # Conditional UPDATE rechecks the revision predicate after acquiring the row lock.
    revised = connection.execute(update(earthquakes).where(
        earthquakes.c.usgs_id == event['usgs_id'],
        earthquakes.c.source_updated_at < event['source_updated_at'],
    ).values(**event, last_seen_at=func.greatest(earthquakes.c.last_seen_at, seen_at))
      .returning(earthquakes.c.usgs_id)).scalar_one_or_none()
    if revised is not None:
        return 'updated'
    connection.execute(update(earthquakes).where(earthquakes.c.usgs_id == event['usgs_id']).values(
        last_seen_at=func.greatest(earthquakes.c.last_seen_at, seen_at)))
    return 'unchanged'


def ingest(engine, settings, client, sleep=time.sleep):
    run_id = uuid4()
    counts = dict.fromkeys(COUNTERS, 0)
    errors = []
    scope = dict(label=COVERAGE, min_latitude=settings.min_latitude,
                 max_latitude=settings.max_latitude, min_longitude=settings.min_longitude,
                 max_longitude=settings.max_longitude)
    with engine.begin() as connection:
        connection.execute(pipeline_runs.insert().values(
            id=run_id, ingestion_type='recent_week', started_at=utcnow(), status='running',
            source=SOURCE, scope=scope, errors=[]))

    def request_failed(detail):
        counts['failed_request_attempts'] += 1
        errors.append(detail)
        # Separate committed transaction survives later ingestion rollback/crash.
        with engine.begin() as connection:
            connection.execute(pipeline_runs.update().where(pipeline_runs.c.id == run_id).values(
                failed_request_attempts=counts['failed_request_attempts'], errors=errors))

    status = 'failed'
    try:
        features = fetch_feed(client, request_failed, sleep)
        counts['fetched'] = len(features)
        with engine.begin() as connection:
            for index, feature in enumerate(features):
                try:
                    event = normalize(feature)
                except ValueError as exc:
                    counts['rejected'] += 1
                    if sum(e['kind'] == 'record' for e in errors) < 100:
                        errors.append({'kind': 'record', 'index': index, 'reason': str(exc)})
                    continue
                if event['event_type'] != 'earthquake':
                    counts['excluded_type'] += 1
                    continue
                if not in_bounds(event, settings):
                    counts['out_of_scope'] += 1
                    continue
                counts['in_scope'] += 1
                counts[upsert_event(connection, event, utcnow())] += 1
        status = 'partial' if counts['rejected'] else 'success'
    except Exception as exc:
        # Data transaction is atomic: these counters mean committed classifications.
        counts.update(inserted=0, updated=0, unchanged=0)
        errors.append({'kind': 'run', 'reason': type(exc).__name__,
                       'detail': str(exc)[:500] if isinstance(exc, (ValueError, RuntimeError))
                       else 'Ingestion failed; data transaction rolled back. Check database availability and schema.'})
    with engine.begin() as connection:
        connection.execute(pipeline_runs.update().where(pipeline_runs.c.id == run_id).values(
            **counts, status=status, ended_at=utcnow(), errors=errors))
    return dict(run_id=str(run_id), status=status, **counts, errors=errors)


def main():
    argparse.ArgumentParser(description='Import real USGS all-week earthquakes in the configured region.').parse_args()
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True,
                          headers={'User-Agent': 'QuakePH-student-project/0.2'}) as client:
            result = ingest(get_engine(), get_settings(), client)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'success' else 1
    except Exception as exc:
        # Persistence itself can be unavailable; never claim a run was recorded then.
        print(json.dumps({'status': 'failed', 'reason': type(exc).__name__,
                          'detail': 'Could not initialize or finalize run tracking; check PostgreSQL and migrations.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
