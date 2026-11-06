from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event as sql_event, select, func, text
from sqlalchemy.exc import OperationalError

from app import api
from app.config import Settings
from app.main import app
from app.ingestion import normalize, upsert_event
from app.models import (earthquakes, pipeline_runs as runs, workers, scheduled_tasks as tasks,
                        reconciliation_events as observations, import_windows)
from app.reconcile import create_reconciliation
from app.coordination import session_lock
from app.worker import SCHEDULE_LOCK
from test_historical import feature

NOW = datetime(2026, 11, 1, 16, 0, 1, tzinfo=timezone.utc)


@pytest.fixture
def dashboard(pg_engine, source_run, monkeypatch):
    monkeypatch.setattr(api, 'get_engine', lambda: pg_engine)
    monkeypatch.setattr(api, 'utcnow', lambda: NOW)
    values = [('a', NOW-timedelta(seconds=2), -1, -2, 'Luzon', 123, 12),
              ('b', NOW-timedelta(seconds=2), 2, 10, 'luzon', 123, 12),
              ('c', NOW-timedelta(seconds=1), 2, -1, None, 123, 12),
              ('d', NOW-timedelta(seconds=1), None, 30, '100%_ Bay', 123, 12),
              ('x', NOW, 9, 5, 'Outside', 140, 30),
              ('z', NOW, 8, 5, 'Deleted', 123, 12)]
    with pg_engine.begin() as conn:
        for identifier, at, magnitude, depth, place, longitude, latitude in values:
            raw = feature(at, identifier)
            raw['geometry']['coordinates'] = [longitude, latitude, depth]
            raw['properties'].update(mag=magnitude, place=place,
                                    url=f'https://earthquake.usgs.gov/earthquakes/eventpage/{identifier}')
            if identifier == 'z':
                raw['properties']['status'] = 'deleted'
            upsert_event(conn, normalize(raw), NOW, source_run)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('filters,expected', [({}, 4), ({'min_magnitude': -1}, 3),
    ({'min_magnitude': 0}, 2), ({'max_magnitude': -1}, 1), ({'max_depth': -1}, 2),
    ({'place': 'LUZ'}, 2), ({'place': '%_'}, 1), ({'place': "' OR 1=1 --"}, 0),
    ({'include_outside': True}, 5), ({'include_deleted': True}, 5),
    ({'include_outside': True, 'include_deleted': True}, 6)])
def test_shared_filters_list_map_stats(dashboard, filters, expected):
    listed = dashboard.get('/api/earthquakes', params=filters).json()
    mapped = dashboard.get('/api/earthquakes/map', params=filters).json()
    stats = dashboard.get('/api/stats', params=filters).json()
    assert listed['total'] == mapped['metadata']['total'] == stats['matching_count'] == expected
    assert {row['usgs_id'] for row in listed['items']} == {f['id'] for f in mapped['features']}
    assert stats['daily']['events_in_chart'] == expected
    assert all('raw_event' not in row for row in listed['items'])


def test_start_end_boundaries_and_manila_days(dashboard):
    params = {'start': '2026-11-01T23:59:59+08:00', 'end': '2026-11-02T00:00:01+08:00'}
    response = dashboard.get('/api/stats', params=params)
    assert response.status_code == 200
    data = response.json()
    assert data['matching_count'] == 4
    assert data['filters']['start'] == '2026-11-01T15:59:59Z'
    assert data['daily']['timezone'] == 'Asia/Manila'
    assert data['daily']['days'] == [{'day': '2026-11-01', 'count': 2}, {'day': '2026-11-02', 'count': 2}]
    params['end'] = '2026-11-02T00:00:00+08:00'
    for path in ['/api/earthquakes', '/api/earthquakes/map', '/api/stats']:
        response = dashboard.get(path, params=params)
        assert response.status_code == 200
        data = response.json()
        assert data.get('total', data.get('matching_count', data.get('metadata', {}).get('total'))) == 2
    assert data['daily']['days'] == [{'day': '2026-11-01', 'count': 2}]


@pytest.mark.parametrize('params', [{'start': '2026-11-01'}, {'end': '2026-11-01T00:00:00'},
    {'start': '1700000000'}, {'start': '2026-11-02T00:00:00Z', 'end': '2026-11-01T00:00:00Z'},
    {'start': '2026-11-01T00:00:00Z', 'end': '2026-11-01T00:00:00Z'}, {'min_magnitude': 'NaN'},
    {'max_depth': 'Infinity'}, {'min_depth': '-Infinity'}, {'min_magnitude': 3, 'max_magnitude': 2},
    {'min_depth': 3, 'max_depth': -1}, {'place': '   '}])
def test_invalid_shared_parameters(dashboard, params):
    for path in ['/api/earthquakes', '/api/earthquakes/map', '/api/stats']:
        assert dashboard.get(path, params=params).status_code == 422


@pytest.mark.parametrize('path,params', [('/api/earthquakes', {'page': 0}),
    ('/api/earthquakes', {'page_size': 101}), ('/api/earthquakes', {'page': 1.5}),
    ('/api/earthquakes', {'sort': 'event_time; DROP TABLE earthquakes'}),
    ('/api/earthquakes/map', {'limit': 5001}), ('/api/earthquakes/map', {'limit': 0}),
    ('/api/stats', {'daily_days': 367}), ('/api/pipeline/runs', {'status': 'made-up'}),
    ('/api/pipeline/runs', {'run_type': 'made-up'}), ('/api/pipeline/runs', {'page_size': 0})])
def test_invalid_pagination_sort_and_limits(dashboard, path, params):
    assert dashboard.get(path, params=params).status_code == 422


def test_stable_pagination_map_truncation_and_stats_full_rows(dashboard):
    ids = []
    for page in range(1, 5):
        response = dashboard.get('/api/earthquakes', params={'page': page, 'page_size': 1})
        assert response.status_code == 200
        assert response.json()['total'] == 4
        ids.append(response.json()['items'][0]['usgs_id'])
    assert ids == ['c', 'd', 'a', 'b']
    assert dashboard.get('/api/earthquakes', params={'sort': 'magnitude_desc'}).json()['items'][-1]['usgs_id'] == 'd'
    mapped = dashboard.get('/api/earthquakes/map', params={'limit': 1}).json()
    assert mapped['type'] == 'FeatureCollection' and mapped['features'][0]['id'] == 'c'
    assert mapped['features'][0]['geometry']['coordinates'] == [123, 12]
    assert mapped['metadata']['returned'] == 1 and mapped['metadata']['total'] == 4 and mapped['metadata']['truncated']
    stats = dashboard.get('/api/stats').json()
    assert stats['matching_count'] == 4 and stats['strongest']['usgs_id'] == stats['latest']['usgs_id'] == 'c'
    assert dashboard.get('/api/earthquakes', params={'page': 99}).json()['items'] == []


def test_empty_results_chart_zeros_and_unknown_ids(dashboard):
    assert dashboard.get('/api/earthquakes/not-stored').status_code == 404
    assert dashboard.get('/api/earthquakes/z').json()['source_state'] == 'deleted'
    result = dashboard.get('/api/stats', params={'place': 'no-match', 'start': '2020-01-01T00:00:00Z',
                                               'end': '2020-01-04T00:00:00Z'}).json()
    assert result['matching_count'] == 0 and result['strongest'] is None and result['latest'] is None
    assert len(result['daily']['days']) == 4  # UTC midnight end includes Jan 4 until 08:00 Manila.
    assert all(item['count'] == 0 for item in result['daily']['days'])
    assert result['daily']['coverage_verified'] is False


def test_chart_bounded_without_losing_overall_statistics(dashboard, pg_engine, source_run):
    with pg_engine.begin() as conn:
        upsert_event(conn, normalize(feature(NOW-timedelta(days=1000), 'old')), NOW, source_run)
    data = dashboard.get('/api/stats', params={'daily_days': 2}).json()
    assert data['matching_count'] == 5 and len(data['daily']['days']) == 2
    assert data['daily']['events_in_chart'] == 4 and data['daily']['events_outside_chart'] == 1
    assert data['daily']['truncated'] is True and data['daily']['coverage_verified'] is False


def test_detail_revisions_bounded_and_current_coverage_uses_coordinates(dashboard, pg_engine, source_run, monkeypatch):
    with pg_engine.begin() as conn:
        row = conn.execute(select(earthquakes).where(earthquakes.c.usgs_id == 'a')).mappings().one()
        raw = deepcopy(row['raw_event'])
        for index in range(21):
            raw['properties']['updated'] += 1000
            raw['geometry']['coordinates'] = [140, 12, -2]
            upsert_event(conn, normalize(raw), NOW+timedelta(seconds=index), source_run)
    assert dashboard.get('/api/earthquakes').json()['total'] == 3
    detail = dashboard.get('/api/earthquakes/a').json()
    assert detail['coverage_status'] == 'outside' and detail['revisions']['total'] == 21
    assert detail['revisions']['returned'] == 20 and detail['revisions']['truncated']
    assert 'raw_event' not in detail
    assert detail['first_seen_at'] == NOW.isoformat().replace('+00:00', 'Z')
    monkeypatch.setattr(api, 'get_settings', lambda: Settings(_env_file=None, max_longitude=150, max_latitude=40))
    detail = dashboard.get('/api/earthquakes/a').json()
    assert detail['coverage_status'] == 'inside' and detail['recorded_coverage_status'] == 'outside'
    assert dashboard.get('/api/earthquakes').json()['total'] == 5


def test_missing_and_unresolved_observations_do_not_imply_deletion(dashboard, pg_engine):
    identifier = create_reconciliation(pg_engine, NOW-timedelta(days=1), NOW+timedelta(days=1), Settings(_env_file=None))
    with pg_engine.begin() as conn:
        conn.execute(observations.update().where(observations.c.reconciliation_id == identifier,
            observations.c.usgs_id == 'a').values(status='missing', checked_at=NOW))
        conn.execute(observations.update().where(observations.c.reconciliation_id == identifier,
            observations.c.usgs_id == 'b').values(status='unresolved', checked_at=NOW))
    result = dashboard.get('/api/earthquakes').json()
    assert result['total'] == 4
    by_id = {row['usgs_id']: row for row in result['items']}
    assert by_id['a']['last_source_observation'] == 'missing' and by_id['a']['source_state'] == 'active'
    assert by_id['b']['last_source_observation'] == 'unresolved'


def test_read_only_snapshot_keeps_count_and_items_consistent(dashboard, pg_engine, source_run):
    inserted = False
    def concurrent_write(conn, cursor, statement, parameters, context, many):
        nonlocal inserted
        if not inserted and 'count(' in statement and 'earthquakes' in statement:
            inserted = True
            with pg_engine.begin() as writer:
                upsert_event(writer, normalize(feature(NOW, 'concurrent')), NOW, source_run)
    sql_event.listen(pg_engine, 'after_cursor_execute', concurrent_write)
    try:
        data = dashboard.get('/api/earthquakes').json()
    finally:
        sql_event.remove(pg_engine, 'after_cursor_execute', concurrent_write)
    assert inserted and data['total'] == len(data['items']) == 4
    assert dashboard.get('/api/earthquakes').json()['total'] == 5
    generator = api.read_connection()
    conn = next(generator)
    assert conn.exec_driver_sql('SHOW transaction_read_only').scalar_one() == 'on'
    assert conn.exec_driver_sql('SHOW transaction_isolation').scalar_one() == 'repeatable read'
    generator.close()


def test_pipeline_no_workers_freshness_failures_and_lock_evidence(dashboard, pg_engine):
    data = dashboard.get('/api/pipeline/status').json()
    assert data['worker_state'] == 'not_observed' and data['workers'] == []
    worker_id, failed_id = uuid4(), uuid4()
    secret = 'postgresql://admin:super-secret@localhost/db /Users/rich/private traceback'
    with pg_engine.begin() as conn:
        conn.execute(workers.insert().values(id=worker_id, hostname=secret, pid=123, started_at=NOW-timedelta(hours=1),
            heartbeat_at=NOW-timedelta(seconds=5), status='running', activity='recent', last_error={'reason': 'RuntimeError', 'detail': secret}))
        conn.execute(runs.insert().values(id=failed_id, ingestion_type='recent_week', status='failed', started_at=NOW,
            source=secret, scope={'database_url': secret}, errors=[{'kind': 'request', 'reason': 'HTTPStatusError', 'http_status': 503, 'detail': secret}]))
        conn.execute(tasks.insert().values(name='recent', interval_seconds=300, next_run_at=NOW+timedelta(seconds=300),
            status='failed', worker_id=worker_id, last_attempt_at=NOW, last_attempt_id=failed_id,
            last_error={'reason': 'task_incomplete', 'outcome': {'secret': secret}}))
    with session_lock(pg_engine, SCHEDULE_LOCK):
        response = dashboard.get('/api/pipeline/status')
    assert response.status_code == 200
    data = response.json()
    assert data['worker_state'] == 'alive' and data['scheduled_lock_held']
    assert data['tasks'][0]['recorded_status'] == 'failed' and data['tasks'][0]['last_success_at'] is None
    assert data['tasks'][0]['owner_confirmation'] == 'not_verified'
    assert 'super-secret' not in response.text and '/Users/' not in response.text and 'traceback' not in response.text
    assert data['recent_failures'][0]['errors']['items'][0]['http_status'] == 503
    with pg_engine.begin() as conn:
        conn.execute(workers.update().values(heartbeat_at=NOW-timedelta(seconds=31)))
        conn.execute(tasks.update().values(status='running'))
    data = dashboard.get('/api/pipeline/status').json()
    assert data['worker_state'] == 'stale' and data['tasks'][0]['recorded_running']
    assert not data['scheduled_lock_held'] and data['tasks'][0]['owner_confirmation'] == 'not_verified'
    with pg_engine.begin() as conn:
        conn.execute(workers.update().values(status='stopped'))
    assert dashboard.get('/api/pipeline/status').json()['worker_state'] == 'stopped'


def test_pipeline_pagination_filters_links_and_error_bounds(dashboard, pg_engine):
    identifier = create_reconciliation(pg_engine, NOW-timedelta(days=1), NOW+timedelta(days=1), Settings(_env_file=None))
    with pg_engine.begin() as conn:
        window = conn.execute(select(import_windows)).mappings().first()
        ids = [uuid4() for _ in range(3)]
        for run_id in ids:
            conn.execute(runs.insert().values(id=run_id, import_window_id=window['id'], ingestion_type='historical_window',
                started_at=NOW, status='failed', source='USGS', scope={}, errors=[{'kind': 'window', 'reason': 'ValueError', 'detail': 'secret'}]*5))
    params = {'run_type': 'historical_window', 'status': 'failed', 'page_size': 1}
    result_ids = []
    for page in range(1, 4):
        data = dashboard.get('/api/pipeline/runs', params=params | {'page': page}).json()
        assert data['total'] == 3
        row = data['items'][0]
        result_ids.append(row['id'])
        assert row['import_job_id'] == str(window['job_id']) and row['reconciliation_id'] == str(identifier)
        assert row['errors']['recorded_count'] == 5 and len(row['errors']['items']) == 3 and row['errors']['truncated']
    assert result_ids == [str(value) for value in sorted(ids, reverse=True)]


def test_no_ingestion_or_write_side_effects_and_cors(dashboard, pg_engine):
    with pg_engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(runs)).scalar_one()
    with patch('app.ingestion.request_usgs', side_effect=AssertionError('read must not contact source')), \
         patch('app.ingestion.ingest', side_effect=AssertionError('read must not import')), \
         patch('app.worker.Worker.run', side_effect=AssertionError('read must not start worker')):
        for path in ['/api/earthquakes', '/api/earthquakes/map', '/api/earthquakes/a', '/api/stats', '/api/pipeline/runs', '/api/pipeline/status']:
            assert dashboard.get(path).status_code == 200
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(runs)).scalar_one() == before
    response = dashboard.options('/api/earthquakes', headers={'Origin': 'http://127.0.0.1:5174', 'Access-Control-Request-Method': 'GET'})
    assert response.status_code == 200 and response.headers['access-control-allow-origin'] == 'http://127.0.0.1:5174'
    assert dashboard.options('/api/earthquakes', headers={'Origin': 'https://unlisted.example', 'Access-Control-Request-Method': 'GET'}).status_code == 400
    assert dashboard.post('/api/earthquakes', json={}).status_code == 405


def test_database_unavailable_is_503_without_connection_details(dashboard, monkeypatch):
    def fail():
        raise OperationalError('postgresql://secret@host /Users/private', {}, Exception('private trace'))
    monkeypatch.setattr(api, 'get_engine', fail)
    response = dashboard.get('/api/earthquakes')
    assert response.status_code == 503 and response.json() == {'detail': 'Database unavailable; retry after PostgreSQL is ready.'}
    assert 'secret' not in response.text and 'private' not in response.text
    assert dashboard.get('/health/live').status_code == 200


def test_empty_future_chart_has_ordered_bounds(dashboard):
    data = dashboard.get('/api/stats', params={'start': '2099-01-01T00:00:00Z'}).json()
    assert data['matching_count'] == 0
    assert data['daily']['start_day'] == data['daily']['end_day'] == '2099-01-01'
    assert data['daily']['days'] == [{'day': '2099-01-01', 'count': 0}]


def test_statistics_strongest_id_tie_and_all_null_magnitudes(dashboard, pg_engine, source_run):
    data = dashboard.get('/api/stats', params={'place': '%_'}).json()
    assert data['matching_count'] == 1 and data['strongest'] is None and data['latest']['usgs_id'] == 'd'
    with pg_engine.begin() as conn:
        row = conn.execute(select(earthquakes).where(earthquakes.c.usgs_id == 'd')).mappings().one()
        raw = deepcopy(row['raw_event']); raw['properties']['updated'] += 1000; raw['properties']['mag'] = 2
        upsert_event(conn, normalize(raw), NOW, source_run)
    assert dashboard.get('/api/stats').json()['strongest']['usgs_id'] == 'c'


def test_openapi_and_origin_configuration():
    from pydantic import ValidationError
    schema = app.openapi()
    assert schema['paths']['/api/earthquakes/map']['get']['responses']['200']['content']['application/json']['schema']
    assert any(p['name'] == 'start' for p in schema['paths']['/api/earthquakes']['get']['parameters'])
    assert Settings(_env_file=None, frontend_origins=['https://example.com/']).frontend_origins == ['https://example.com']
    for origin in ['*', 'postgresql://secret@host', 'https://example.com/private', 'https://user:secret@example.com']:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, frontend_origins=[origin])


def test_one_shot_recent_collection_visible_without_worker(dashboard, pg_engine):
    identifier = uuid4()
    with pg_engine.begin() as conn:
        conn.execute(runs.insert().values(id=identifier, ingestion_type='recent_week', status='success',
            started_at=NOW-timedelta(seconds=10), ended_at=NOW, source='fixture', scope={}, errors=[]))
    result = dashboard.get('/api/pipeline/status').json()
    assert result['worker_state'] == 'not_observed'
    assert result['tasks'] == []
    assert datetime.fromisoformat(result['recent_success_at'].replace('Z', '+00:00')) == NOW
    assert datetime.fromisoformat(result['recent_attempt_at'].replace('Z', '+00:00')) == NOW-timedelta(seconds=10)
