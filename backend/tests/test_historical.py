import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event as sql_event, func, select

from app.config import Settings
from app.historical import (create_job, execute_job, parse_date, JobBusy, job_lock,
                            owned_transaction, OwnershipLost, main)
from app.models import earthquakes, import_jobs as jobs, import_windows as windows, pipeline_runs as runs

START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def feature(at, identifier='fixture'):
    milliseconds = int(at.timestamp() * 1000)
    return dict(type='Feature', id=identifier,
                geometry=dict(type='Point', coordinates=[123, 12, -1]),
                properties=dict(time=milliseconds, updated=milliseconds + 100,
                                type='earthquake', mag=None, place=None))


def feed(features):
    return dict(type='FeatureCollection', metadata=dict(count=len(features), status=200), features=features)


class Catalog:
    """Controlled catalog with USGS-style inclusive time boundaries."""
    def __init__(self, features=()):
        self.features = list(features)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        params = request.url.params
        start, end = [datetime.fromisoformat(params[k]) for k in ('starttime', 'endtime')]
        records = [f for f in self.features if start.timestamp()*1000 <= f['properties']['time'] <= end.timestamp()*1000]
        if request.url.path.endswith('/count'):
            return httpx.Response(200, text=str(len(records)))
        return httpx.Response(200, json=feed(records[:int(params['limit'])]))


def job(engine, days=1, window_days=7):
    return create_job(engine, START, START + timedelta(days=days), Settings(_env_file=None), window_days)


def execute(engine, job_id, handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return execute_job(engine, job_id, client, sleep=lambda _: None)


def cap(engine, job_id, limit):
    with engine.begin() as connection:
        settings = connection.execute(select(jobs.c.settings).where(jobs.c.id == job_id)).scalar_one()
        connection.execute(jobs.update().where(jobs.c.id == job_id).values(settings=settings | {'result_limit': limit}))


@pytest.mark.parametrize('value', ['2020-02-30', '20200101', '2020-1-01', '2020-01-01T00:00:00Z', 'junk'])
def test_invalid_dates(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_date(value)


def test_utc_date_and_reverse_ranges(pg_engine, monkeypatch):
    assert parse_date('2020-01-01') == START
    with pytest.raises(ValueError, match='start <'):
        create_job(pg_engine, START, START, Settings(_env_file=None))
    with pytest.raises(ValueError):
        create_job(pg_engine, START + timedelta(days=1), START, Settings(_env_file=None))
    monkeypatch.setattr('sys.argv', ['historical', 'import', '--start', '2020-01-02', '--end', '2020-01-01'])
    with patch('app.historical.get_engine', side_effect=AssertionError('invalid range must not touch DB')):
        with pytest.raises(SystemExit) as result:
            main()
    assert result.value.code == 2


def test_adjacent_windows_start_included_end_excluded_and_reimport(pg_engine):
    boundary = START + timedelta(days=1)
    catalog = Catalog([feature(START, 'at-start'), feature(boundary, 'at-shared'),
                       feature(START + timedelta(days=2), 'at-end')])
    first = execute(pg_engine, job(pg_engine, days=2, window_days=1), catalog)
    assert first['status'] == 'success'
    assert first['windows']['completed'] == 2
    assert first['committed_counters']['inserted'] == 2
    assert first['committed_counters']['outside_time'] == 2
    assert first['committed_counters']['fetched'] == 4
    with pg_engine.connect() as connection:
        assert set(connection.execute(select(earthquakes.c.usgs_id)).scalars()) == {'at-start', 'at-shared'}
    again = execute(pg_engine, job(pg_engine, days=2, window_days=1), Catalog(catalog.features))
    assert again['committed_counters']['inserted'] == 0
    assert again['committed_counters']['unchanged'] == 2
    for request in catalog.requests:
        assert request.url.params['eventtype'] == 'earthquake'
        assert float(request.url.params['minlatitude']) == 4
        if request.url.path.endswith('/query'):
            assert request.url.params['orderby'] == 'time-asc'
            assert request.url.params['format'] == 'geojson'
            assert request.url.params['limit'] == '20000'


def test_oversized_window_subdivides(pg_engine):
    identifier = job(pg_engine)
    cap(pg_engine, identifier, 3)
    catalog = Catalog([feature(START + timedelta(hours=h), str(h)) for h in (1, 5, 19)])
    result = execute(pg_engine, identifier, catalog)
    assert result['status'] == 'success'
    assert result['windows'] == dict(pending=0, running=0, completed=2, failed=0, split=1)
    assert result['window_attempts'] == 3
    assert result['committed_counters']['inserted'] == 3
    assert len([r for r in catalog.requests if r.url.path.endswith('/query')]) == 2


@pytest.mark.parametrize('race', ['saturated_response', 'http400'])
def test_count_query_race_subdivides_without_accepting_truncated_results(pg_engine, race):
    identifier = job(pg_engine)
    cap(pg_engine, identifier, 3)
    catalog = Catalog([feature(START + timedelta(hours=h), str(h)) for h in (1, 5, 19)])
    root_count_requests = 0
    def handler(request):
        nonlocal root_count_requests
        params = request.url.params
        is_root = datetime.fromisoformat(params['endtime']) - datetime.fromisoformat(params['starttime']) == timedelta(days=1)
        if is_root and request.url.path.endswith('/count'):
            root_count_requests += 1
            return httpx.Response(200, text='0' if root_count_requests == 1 else '3')
        if is_root and race == 'http400':
            return httpx.Response(400)
        return catalog(request)
    result = execute(pg_engine, identifier, handler)
    assert result['status'] == 'success' and result['windows']['split'] == 1
    assert result['committed_counters']['inserted'] == 3
    assert result['total_failed_request_attempts'] == (1 if race == 'http400' else 0)


def test_minimum_window_overflow_is_failure(pg_engine):
    identifier = create_job(pg_engine, START, START + timedelta(milliseconds=1), Settings(_env_file=None))
    cap(pg_engine, identifier, 1)
    result = execute(pg_engine, identifier, Catalog([feature(START)]))
    assert result['status'] == 'failed' and result['windows']['failed'] == 1
    assert result['committed_counters']['inserted'] == 0
    assert 'Minimum 1ms' in result['last_error']['detail']


@pytest.mark.parametrize('empty_status', [200, 204])
def test_empty_window_and_completed_resume_needs_no_requests(pg_engine, empty_status):
    identifier = job(pg_engine)
    def empty(request):
        if request.url.path.endswith('/count'):
            return httpx.Response(200, text='0')
        return httpx.Response(empty_status, json=feed([]) if empty_status == 200 else None)
    result = execute(pg_engine, identifier, empty)
    assert result['status'] == 'success' and result['committed_counters']['fetched'] == 0
    def unexpected(_):
        raise AssertionError('completed jobs must not be fetched again')
    assert execute(pg_engine, identifier, unexpected) == result


def test_failure_preserves_checkpoint_resume_uses_original_scope(pg_engine, monkeypatch):
    identifier = job(pg_engine, days=2, window_days=1)
    catalog = Catalog([feature(START, 'first'), feature(START + timedelta(days=1), 'second')])
    def fail_second(request):
        if datetime.fromisoformat(request.url.params['starttime']) >= START + timedelta(days=1):
            return httpx.Response(503)
        return catalog(request)
    failed = execute(pg_engine, identifier, fail_second)
    assert failed['status'] == 'failed'
    assert failed['windows']['completed'] == 1 and failed['windows']['failed'] == 1
    assert failed['committed_counters']['inserted'] == 1
    assert failed['total_failed_request_attempts'] == 3
    monkeypatch.setenv('MIN_LATITUDE', '9')
    resumed_requests = []
    def resumed(request):
        resumed_requests.append(request)
        assert datetime.fromisoformat(request.url.params['starttime']) == START + timedelta(days=1)
        assert float(request.url.params['minlatitude']) == 4
        return catalog(request)
    complete = execute(pg_engine, identifier, resumed)
    assert complete['status'] == 'success' and complete['executions'] == 2
    assert complete['windows']['completed'] == 2 and complete['window_attempts'] == 3
    assert complete['committed_counters']['inserted'] == 2
    assert complete['total_failed_request_attempts'] == 3
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 2
        attempts = connection.execute(select(runs).order_by(runs.c.started_at)).mappings().all()
    assert [a['status'] for a in attempts] == ['success', 'failed', 'success']
    assert attempts[1]['failed_request_attempts'] == 3


def test_interrupted_window_recovers_only_after_lock_release(pg_engine):
    identifier = job(pg_engine)
    def interrupted(_):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute(pg_engine, identifier, interrupted)
    with pg_engine.connect() as connection:
        assert connection.execute(select(jobs.c.status)).scalar_one() == 'running'
        assert connection.execute(select(windows.c.status)).scalar_one() == 'running'
    result = execute(pg_engine, identifier, Catalog([feature(START)]))
    assert result['status'] == 'success' and result['window_attempts'] == 2
    with pg_engine.connect() as connection:
        history = connection.execute(select(runs).order_by(runs.c.started_at)).mappings().all()
    assert [h['status'] for h in history] == ['failed', 'success']
    assert history[0]['errors'][-1]['kind'] == 'interrupted'


def test_same_job_concurrent_execution_refused_without_recovery(pg_engine):
    identifier = job(pg_engine)
    entered, release = Event(), Event()
    catalog = Catalog()
    def blocked(request):
        entered.set()
        assert release.wait(10), 'test synchronization timed out'
        return catalog(request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(execute, pg_engine, identifier, blocked)
        try:
            assert entered.wait(10)
            with pytest.raises(JobBusy):
                execute(pg_engine, identifier, Catalog())
            with pg_engine.connect() as connection:
                assert connection.execute(select(windows.c.attempts)).scalar_one() == 1
                assert connection.execute(select(runs.c.status)).scalar_one() == 'running'
        finally:
            release.set()
        assert first.result(timeout=10)['status'] == 'success'


def test_old_executor_token_cannot_write_after_new_owner(pg_engine):
    identifier = job(pg_engine)
    old_token, new_token = uuid4(), uuid4()
    with pg_engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.id == identifier).values(owner_token=new_token))
    with pytest.raises(OwnershipLost):
        with owned_transaction(pg_engine, identifier, old_token):
            pytest.fail('stale owner entered a write transaction')
    with owned_transaction(pg_engine, identifier, new_token):
        pass


def test_rejected_record_rolls_back_window_and_records_reason(pg_engine):
    identifier = job(pg_engine)
    def malformed(request):
        return httpx.Response(200, text='2') if request.url.path.endswith('/count') else httpx.Response(200, json=feed([feature(START), {}]))
    result = execute(pg_engine, identifier, malformed)
    assert result['status'] == 'failed'
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 0
        attempt = connection.execute(select(runs)).mappings().one()
    assert attempt['rejected'] == 1 and attempt['inserted'] == 0
    assert attempt['errors'][0]['kind'] == 'record'
    assert execute(pg_engine, identifier, Catalog([feature(START)]))['status'] == 'success'


def test_checkpoint_failure_rolls_back_event_writes(pg_engine):
    identifier = job(pg_engine)
    def fault(connection, cursor, statement, parameters, context, executemany):
        if 'UPDATE' in statement and 'import_windows SET' in statement and parameters.get('status') == 'completed':
            raise RuntimeError('controlled checkpoint failure')
    sql_event.listen(pg_engine, 'before_cursor_execute', fault)
    try:
        result = execute(pg_engine, identifier, Catalog([feature(START)]))
    finally:
        sql_event.remove(pg_engine, 'before_cursor_execute', fault)
    assert result['status'] == 'failed'
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 0
        assert connection.execute(select(windows.c.status)).scalar_one() == 'failed'
    assert execute(pg_engine, identifier, Catalog([feature(START)]))['committed_counters']['inserted'] == 1


@pytest.mark.parametrize('status,expected', [('success', 0), ('failed', 1), ('running', 1)])
def test_historical_cli_resume_exit_codes(monkeypatch, status, expected, capsys):
    identifier = uuid4()
    monkeypatch.setattr('sys.argv', ['historical', 'resume', str(identifier)])
    with patch('app.historical.get_engine'), patch('app.historical.execute_job', return_value={'status': status}):
        assert main() == expected
    assert str(identifier) in capsys.readouterr().out


def test_permanent_bad_query_is_not_misclassified_as_overflow(pg_engine):
    identifier = job(pg_engine)
    calls = []
    def bad_query(request):
        calls.append(request.url.path)
        return httpx.Response(200, text='0') if request.url.path.endswith('/count') else httpx.Response(400)
    result = execute(pg_engine, identifier, bad_query)
    assert result['status'] == 'failed'
    assert result['windows']['split'] == 0 and result['window_attempts'] == 1
    assert result['total_failed_request_attempts'] == 1
    assert len(calls) == 3  # count, failed query, confirming count; no retry loop


def test_catalog_metadata_count_optional_but_envelope_still_validated(pg_engine):
    identifier = job(pg_engine)
    def empty_without_count(request):
        if request.url.path.endswith('/count'):
            return httpx.Response(200, text='0')
        return httpx.Response(200, json=dict(type='FeatureCollection',
                                           metadata=dict(status=200, limit=20000, offset=1), features=[]))
    result = execute(pg_engine, identifier, empty_without_count)
    assert result['status'] == 'success' and result['committed_counters']['fetched'] == 0
    from app.ingestion import validate_feed
    for invalid in [{}, {'type': 'FeatureCollection', 'metadata': {'status': 200}, 'features': None},
                    {'type': 'FeatureCollection', 'metadata': {'status': 200, 'count': 1}, 'features': []}]:
        with pytest.raises(ValueError):
            validate_feed(invalid, require_count=False)
