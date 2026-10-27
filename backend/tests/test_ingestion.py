from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select, func

from app.config import Settings
from app.ingestion import (normalize, timestamp, in_bounds, validate_feed, retry_delay,
                           fetch_feed, ingest, upsert_event)
from app.models import earthquakes, pipeline_runs


def feature(updated=1700000000001, event_id='fixture-1', **properties):
    return {'type': 'Feature', 'id': event_id,
            'geometry': {'type': 'Point', 'coordinates': [123.4, 12.3, -2.5]},
            'properties': dict(time=1700000000000, updated=updated, type='earthquake',
                               mag=-0.5, place=None, magType=None, **properties)}


def feed(*features):
    return {'type': 'FeatureCollection', 'metadata': {'status': 200, 'count': len(features)},
            'features': list(features)}


def client_for(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))


def test_coordinates_milliseconds_and_nullable_fields():
    event = normalize(feature())
    assert (event['longitude'], event['latitude'], event['depth_km']) == (123.4, 12.3, -2.5)
    assert event['event_time'] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert event['source_updated_at'].microsecond == 1000
    assert event['place'] is None and event['magnitude_type'] is None
    assert event['magnitude'] == -0.5
    raw = feature(); raw['properties']['mag'] = None
    assert normalize(raw)['magnitude'] is None
    assert timestamp(-1, 'time') == datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)


@pytest.mark.parametrize('field,value', [('mag', float('nan')), ('mag', float('inf')),
    ('mag', True), ('time', None), ('updated', 'yesterday'), ('time', 1.5),
    ('updated', 10**100), ('place', 3)])
def test_bad_properties(field, value):
    raw = feature(); raw['properties'][field] = value
    with pytest.raises(ValueError):
        normalize(raw)


@pytest.mark.parametrize('coords', [[181, 10, 2], [120, -91, 2], [120, 10],
                                   [120, 10, float('inf')], [True, 10, 2]])
def test_bad_coordinates(coords):
    raw = feature(); raw['geometry']['coordinates'] = coords
    with pytest.raises(ValueError):
        normalize(raw)


@pytest.mark.parametrize('raw', [None, {}, {'type': 'Feature'},
                                  {'type': 'Feature', 'id': ' '}])
def test_malformed_records(raw):
    with pytest.raises(ValueError):
        normalize(raw)


@pytest.mark.parametrize('lon,lat,expected', [(116, 4, True), (130, 22, True),
    (115.99, 10, False), (131, 10, False), (120, 3.99, False), (120, 22.01, False)])
def test_bounds(lon, lat, expected):
    assert in_bounds({'longitude': lon, 'latitude': lat}, Settings(_env_file=None)) is expected


def test_empty_feed_is_valid_but_invalid_envelopes_are_not():
    assert validate_feed(feed()) == []
    for invalid in [{}, [], {'type': 'FeatureCollection', 'features': []},
                    {'type': 'FeatureCollection', 'features': [], 'metadata': {'status': 500, 'count': 0}},
                    feed(feature()) | {'features': []}]:
        with pytest.raises(ValueError):
            validate_feed(invalid)


def test_retry_after_bound_and_date():
    assert retry_delay('9999', 1) == 30
    assert retry_delay('-5', 1) == 0
    assert retry_delay('not-a-date', 2) == 2
    with patch('app.ingestion.utcnow', return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)):
        assert retry_delay('Thu, 01 Jan 2026 00:00:05 GMT', 1) == 5


@pytest.mark.parametrize('status,attempts', [(404, 1), (429, 3), (503, 3)])
def test_bounded_http_failures(status, attempts):
    failures, delays = [], []
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        with pytest.raises(RuntimeError):
            fetch_feed(client, failures.append, delays.append)
    assert len(failures) == attempts
    assert len(delays) == attempts - 1


def test_duplicate_and_revision_content_rules(pg_engine):
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    original = normalize(feature())
    newer_raw = feature(updated=1700000001000); newer_raw['properties']['mag'] = 5.2
    newer = normalize(newer_raw)
    same_revision = deepcopy(newer); same_revision['place'] = 'must not overwrite'
    for index, (event, expected) in enumerate([
        (original, 'inserted'), (original, 'unchanged'), (newer, 'updated'),
        (original, 'unchanged'), (same_revision, 'unchanged')]):
        with pg_engine.begin() as connection:
            assert upsert_event(connection, event, first + timedelta(seconds=index)) == expected
    with pg_engine.connect() as connection:
        rows = connection.execute(select(earthquakes)).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row['magnitude'] == 5.2 and row['place'] is None
    assert row['raw_event'] == newer_raw
    assert row['first_seen_at'] == first
    assert row['last_seen_at'] == first + timedelta(seconds=4)
    with pg_engine.begin() as connection:
        upsert_event(connection, original, first - timedelta(seconds=1))
        assert connection.execute(select(earthquakes.c.last_seen_at)).scalar_one() == row['last_seen_at']


def test_concurrent_inserts_and_revisions(pg_engine):
    def store(revision):
        with pg_engine.begin() as connection:
            return upsert_event(connection, normalize(feature(updated=1700000000000 + revision)),
                                datetime.now(timezone.utc))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(store, [2, 1, 5, 3, 5, 0, 4, 5]))
    assert results.count('inserted') == 1
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 1
        assert connection.execute(select(earthquakes.c.source_updated_at)).scalar_one() == timestamp(1700000000005, 'updated')


def test_run_filtering_partial_and_empty(pg_engine):
    other = feature(event_id='explosion'); other['properties']['type'] = 'explosion'
    outside = feature(event_id='outside'); outside['geometry']['coordinates'][0] = 0
    with client_for(feed(feature(), other, outside, {})) as client:
        result = ingest(pg_engine, Settings(_env_file=None), client)
    assert result['status'] == 'partial'
    assert [result[k] for k in ['fetched', 'in_scope', 'inserted', 'updated', 'unchanged',
                              'rejected', 'excluded_type', 'out_of_scope']] == [4, 1, 1, 0, 0, 1, 1, 1]
    assert result['errors'][0]['reason']
    with client_for(feed()) as client:
        assert ingest(pg_engine, Settings(_env_file=None), client)['status'] == 'success'
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 1
        assert len(connection.execute(select(pipeline_runs)).all()) == 2


def test_retry_then_success_records_failed_attempt(pg_engine):
    replies = iter([httpx.Response(429, headers={'Retry-After': '2'}), httpx.Response(200, json=feed())])
    delays = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: next(replies))) as client:
        result = ingest(pg_engine, Settings(_env_file=None), client, delays.append)
    assert result['status'] == 'success' and result['failed_request_attempts'] == 1
    assert delays == [2]
    with pg_engine.connect() as connection:
        run = connection.execute(select(pipeline_runs)).mappings().one()
    assert run['failed_request_attempts'] == 1 and run['errors'][0]['http_status'] == 429


def test_network_failure_persisted(pg_engine):
    def fail(request):
        raise httpx.ConnectError('fixture failure', request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        result = ingest(pg_engine, Settings(_env_file=None), client, lambda _: None)
    assert result['status'] == 'failed' and result['failed_request_attempts'] == 3
    with pg_engine.connect() as connection:
        run = connection.execute(select(pipeline_runs)).mappings().one()
    assert run['status'] == 'failed' and run['ended_at'] is not None
    assert run['failed_request_attempts'] == 3


def test_rollback_preserves_failed_run_and_attempts(pg_engine):
    from app.ingestion import upsert_event as real_upsert
    replies = iter([httpx.Response(503), httpx.Response(200, json=feed(feature()))])
    def insert_then_fail(connection, event, seen):
        real_upsert(connection, event, seen)
        raise RuntimeError('controlled transaction failure')
    with httpx.Client(transport=httpx.MockTransport(lambda r: next(replies))) as client:
        with patch('app.ingestion.upsert_event', side_effect=insert_then_fail):
            result = ingest(pg_engine, Settings(_env_file=None), client, lambda _: None)
    assert result['status'] == 'failed' and result['inserted'] == 0
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 0
        run = connection.execute(select(pipeline_runs)).mappings().one()
    assert run['status'] == 'failed' and run['failed_request_attempts'] == 1
    assert run['errors'][-1]['detail'] == 'controlled transaction failure'


def test_invalid_response_run_is_failed(pg_engine):
    with client_for({}) as client:
        result = ingest(pg_engine, Settings(_env_file=None), client)
    assert result['status'] == 'failed' and result['fetched'] == 0
    assert 'invalid USGS response' in result['errors'][-1]['detail']


@pytest.mark.parametrize('status,expected', [('success', 0), ('partial', 1), ('failed', 1)])
def test_cli_exit_codes(monkeypatch, status, expected, capsys):
    from app.ingestion import main
    monkeypatch.setattr('sys.argv', ['ingestion'])
    with patch('app.ingestion.ingest', return_value={'status': status}):
        assert main() == expected
    assert status in capsys.readouterr().out


def test_cli_tracking_unavailable_exits_nonzero(monkeypatch, capsys):
    from app.ingestion import main
    monkeypatch.setattr('sys.argv', ['ingestion'])
    with patch('app.ingestion.ingest', side_effect=RuntimeError('secret-url')):
        assert main() == 1
    output = capsys.readouterr().out
    assert 'Could not initialize or finalize run tracking' in output
    assert 'secret-url' not in output
