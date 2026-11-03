from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import signal
from threading import Event
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select, func, text, event as sql_event

from app.config import Settings
from app.coordination import session_lock, OwnershipLost
from app.ingestion import ingest, normalize, upsert_event, utcnow
from app.historical import create_job, execute_job
from app.reconcile import create_reconciliation, execute_reconciliation
from app.worker import Worker, shutdown_signals
from app.models import (earthquakes, revision_history, pipeline_runs as runs, workers,
                        scheduled_tasks as tasks, reconciliations, reconciliation_events)
from test_historical import START, feature, feed


class Source:
    def __init__(self, features=()):
        self.features, self.ids, self.requests = list(features), {}, []

    def __call__(self, request):
        self.requests.append(request)
        params = request.url.params
        if 'eventid' in params:
            assert params['includedeleted'] == 'true'
            assert 'minlatitude' not in params and 'starttime' not in params
            found = self.ids.get(params['eventid'], next((f for f in self.features if f['id'] == params['eventid']), None))
            return found if isinstance(found, httpx.Response) else httpx.Response(200, json=found) if found else httpx.Response(404)
        if 'summary' in request.url.path:
            return httpx.Response(200, json=feed(self.features))
        start, end = [datetime.fromisoformat(params[k]) for k in ('starttime', 'endtime')]
        chosen = [f for f in self.features if start.timestamp()*1000 <= f['properties']['time'] <= end.timestamp()*1000
                  and float(params['minlatitude']) <= f['geometry']['coordinates'][1] <= float(params['maxlatitude'])
                  and float(params['minlongitude']) <= f['geometry']['coordinates'][0] <= float(params['maxlongitude'])]
        return httpx.Response(200, text=str(len(chosen))) if request.url.path.endswith('/count') else httpx.Response(200, json=feed(chosen))


def settings(**values):
    return Settings(_env_file=None, worker_recent_seconds=60, worker_retry_seconds=1,
                    worker_poll_seconds=0.1, **values)


def collect(engine, source):
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        return ingest(engine, settings(), client, lambda _: None)


def reconcile(engine, identifier, source):
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        return execute_reconciliation(engine, identifier, client, lambda _: None)


def test_history_newer_older_same_and_timestamp_only(pg_engine, source_run):
    original = feature(START)
    event = normalize(original)
    now = utcnow()
    with pg_engine.begin() as conn:
        upsert_event(conn, event, now, source_run)
    newer = deepcopy(event); newer['source_updated_at'] += timedelta(seconds=1); newer['magnitude'] = 5
    with pg_engine.begin() as conn:
        assert upsert_event(conn, newer, now + timedelta(seconds=1), source_run) == 'updated'
        assert upsert_event(conn, event, now + timedelta(seconds=2), source_run) == 'unchanged'
        assert upsert_event(conn, newer, now + timedelta(seconds=3), source_run) == 'unchanged'
        timestamp_only = dict(newer, source_updated_at=newer['source_updated_at'] + timedelta(seconds=1))
        upsert_event(conn, timestamp_only, now + timedelta(seconds=4), source_run)
    with pg_engine.connect() as conn:
        history = conn.execute(select(revision_history).order_by(revision_history.c.new_updated_at)).mappings().all()
        current = conn.execute(select(earthquakes)).mappings().one()
    assert len(history) == 2 and history[0]['changes'] == {'magnitude': {'old': None, 'new': 5}}
    assert history[1]['changes'] == {} and all(h['pipeline_run_id'] == source_run for h in history)
    assert current['first_seen_at'] == now and current['last_seen_at'] == now + timedelta(seconds=4)


def test_concurrent_revision_history_once_and_atomic_rollback(pg_engine, source_run):
    original = normalize(feature(START))
    with pg_engine.begin() as conn:
        upsert_event(conn, original, utcnow(), source_run)
    newer = dict(original, magnitude=4, source_updated_at=original['source_updated_at'] + timedelta(seconds=1))
    def write(_):
        with pg_engine.begin() as conn:
            return upsert_event(conn, newer, utcnow(), source_run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ['unchanged', 'updated']
    def fail_history(conn, cursor, statement, parameters, context, many):
        if 'INSERT INTO' in statement and 'revision_history' in statement:
            raise RuntimeError('controlled history failure')
    sql_event.listen(pg_engine, 'before_cursor_execute', fail_history)
    try:
        with pytest.raises(RuntimeError):
            with pg_engine.begin() as conn:
                upsert_event(conn, dict(newer, magnitude=6, source_updated_at=newer['source_updated_at']+timedelta(seconds=1)), utcnow(), source_run)
    finally:
        sql_event.remove(pg_engine, 'before_cursor_execute', fail_history)
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(revision_history)).scalar_one() == 1
        assert conn.execute(select(earthquakes.c.magnitude)).scalar_one() == 4


def test_history_applies_to_recent_and_historical(pg_engine):
    original = feature(START)
    source = Source([original])
    assert collect(pg_engine, source)['inserted'] == 1
    revised = deepcopy(original); revised['properties']['updated'] += 1000; revised['properties']['place'] = 'new description'
    source.features = [revised]
    assert collect(pg_engine, source)['updated'] == 1
    revised = deepcopy(revised); revised['properties']['updated'] += 1000; revised['properties']['mag'] = 3
    source.features = [revised]
    identifier = create_job(pg_engine, START, START + timedelta(days=1), settings())
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        assert execute_job(pg_engine, identifier, client)['status'] == 'success'
    with pg_engine.connect() as conn:
        kinds = conn.execute(select(runs.c.ingestion_type).join(revision_history, runs.c.id == revision_history.c.pipeline_run_id)).scalars().all()
    assert sorted(kinds) == ['historical_window', 'recent_week']


def test_moved_outside_region_rechecked_and_not_deleted(pg_engine):
    original = feature(START)
    source = Source([original]); collect(pg_engine, source)
    identifier = create_reconciliation(pg_engine, START, START + timedelta(days=1), settings())
    moved = deepcopy(original); moved['geometry']['coordinates'] = [140, 30, 5]; moved['properties']['updated'] += 1000
    source.features = [moved]
    outcome = reconcile(pg_engine, identifier, source)
    assert outcome['status'] == 'success' and outcome['recheck_counters']['updated'] == 1
    with pg_engine.connect() as conn:
        current = conn.execute(select(earthquakes)).mappings().one()
        history = conn.execute(select(revision_history)).mappings().one()
    assert current['longitude'] == 140 and current['coverage_status'] == 'outside'
    assert current['source_state'] == 'active' and history['changes']['latitude']['new'] == 30


@pytest.mark.parametrize('observation', ['missing', 'unresolved', 'deleted', 'redirected'])
def test_explicit_source_observations(pg_engine, observation):
    original = feature(START)
    source = Source([original]); collect(pg_engine, source)
    identifier = create_reconciliation(pg_engine, START, START + timedelta(days=1), settings())
    source.features = []
    if observation == 'missing':
        source.ids['fixture'] = httpx.Response(404)
    elif observation == 'unresolved':
        source.ids['fixture'] = httpx.Response(409)
    elif observation == 'deleted':
        tombstone = deepcopy(original); tombstone['geometry'] = None
        tombstone['properties'].update(status='deleted', updated=original['properties']['updated'] + 1000)
        source.ids['fixture'] = tombstone
    else:
        canonical = deepcopy(original); canonical['id'] = 'canonical'; source.ids['fixture'] = canonical
    outcome = reconcile(pg_engine, identifier, source)
    assert outcome['status'] == 'success' and outcome['id_observations'][observation] == 1
    with pg_engine.connect() as conn:
        current = conn.execute(select(earthquakes).where(earthquakes.c.usgs_id == 'fixture')).mappings().one()
    assert current['source_state'] == ('deleted' if observation == 'deleted' else 'active')
    assert current['source_checked_at'] is not None


def test_reconciliation_resume_budget_failure_and_new_period_refetch(pg_engine):
    source = Source([feature(START, 'a'), feature(START + timedelta(hours=1), 'b')])
    collect(pg_engine, source)
    identifier = create_reconciliation(pg_engine, START, START + timedelta(days=1), settings(recheck_max_ids=1))
    first = reconcile(pg_engine, identifier, source)
    assert first['status'] == 'incomplete' and first['id_observations']['found'] == 1
    source.ids['b'] = httpx.Response(503)
    failed = reconcile(pg_engine, identifier, source)
    assert failed['status'] == 'failed' and failed['id_observations']['found'] == 1
    source.ids.pop('b'); source.requests.clear()
    assert reconcile(pg_engine, identifier, source)['status'] == 'success'
    assert [r.url.params['eventid'] for r in source.requests] == ['b']
    source.requests.clear()
    new = create_reconciliation(pg_engine, START, START + timedelta(days=1), settings())
    assert reconcile(pg_engine, new, source)['status'] == 'success'
    assert any(r.url.path.endswith('/count') for r in source.requests)


class Clock:
    def __init__(self):
        self.now = START + timedelta(days=2)
    def __call__(self):
        return self.now


def test_schedules_restart_overdue_once_and_fresh_reconciliation(pg_engine):
    clock, source = Clock(), Source()
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        first = Worker(pg_engine, settings(), clock, client=client)
        assert first.tick()['task'] == 'recent'
        first_reconcile = first.tick()
        assert first_reconcile['task'] == 'reconcile'
        first.close()
        second = Worker(pg_engine, settings(), clock, client=client)
        assert second.tick() is None
        clock.now += timedelta(days=3)
        assert second.tick()['task'] == 'recent'
        later = second.tick()
        assert later['task'] == 'reconcile' and later['attempt_id'] != first_reconcile['attempt_id']
        assert second.tick() is None
        second.close()
    with pg_engine.connect() as conn:
        assert set(conn.execute(select(tasks.c.attempts)).scalars()) == {2}
        assert conn.execute(select(func.count()).select_from(reconciliations)).scalar_one() == 2


def test_two_workers_do_not_overlap_or_recover_active_run(pg_engine):
    source, entered, release = Source(), Event(), Event()
    def blocked(request):
        entered.set(); assert release.wait(10)
        return source(request)
    with httpx.Client(transport=httpx.MockTransport(blocked)) as client:
        first = Worker(pg_engine, settings(), Clock(), client=client)
        second = Worker(pg_engine, settings(), Clock(), client=client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt = pool.submit(first.tick)
            try:
                assert entered.wait(10)
                assert second.tick() is None
                with pg_engine.connect() as conn:
                    assert conn.execute(select(runs.c.status)).scalar_one() == 'running'
                    assert conn.execute(select(tasks.c.attempts).where(tasks.c.name == 'recent')).scalar_one() == 1
            finally:
                release.set()
            assert attempt.result(10)['outcome']['status'] == 'success'
        first.close(); second.close()


def test_session_lock_idle_release_and_connection_loss(pg_engine):
    with session_lock(pg_engine, 'fixture-lock') as guard:
        with pg_engine.connect() as conn:
            state = conn.execute(text('SELECT state, xact_start FROM pg_stat_activity WHERE pid=:pid'), {'pid': guard.pid}).one()
            assert state.state == 'idle' and state.xact_start is None
            conn.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': guard.pid})
        with pytest.raises(OwnershipLost):
            guard.check()
    with session_lock(pg_engine, 'fixture-lock') as recovered:
        recovered.check()


def test_interrupted_scheduled_run_recovered_after_owner_exit(pg_engine):
    def interrupted(_):
        raise KeyboardInterrupt()
    with httpx.Client(transport=httpx.MockTransport(interrupted)) as client:
        first = Worker(pg_engine, settings(), Clock(), client=client)
        with pytest.raises(KeyboardInterrupt):
            first.tick()
    with pg_engine.connect() as conn:
        previous_id = conn.execute(select(runs.c.id)).scalar_one()
    source = Source()
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        second = Worker(pg_engine, settings(), Clock(), client=client)
        assert second.tick()['outcome']['status'] == 'success'
        second.close()
    with pg_engine.connect() as conn:
        previous = conn.execute(select(runs).where(runs.c.id == previous_id)).mappings().one()
    assert previous['status'] == 'failed' and previous['errors'][-1]['reason'] == 'interrupted'
    first.close()


def test_sigterm_graceful_stop_no_success_and_heartbeat_separate(pg_engine):
    stop = Event()
    def terminate(request):
        signal.raise_signal(signal.SIGTERM)
        return httpx.Response(200, json=feed([]))
    with httpx.Client(transport=httpx.MockTransport(terminate)) as client:
        worker = Worker(pg_engine, settings(), Clock(), stop=stop, client=client)
        with shutdown_signals(stop):
            assert worker.run() == 0
    with pg_engine.connect() as conn:
        current = conn.execute(select(workers).where(workers.c.id == worker.id)).mappings().one()
        task = conn.execute(select(tasks).where(tasks.c.name == 'recent')).mappings().one()
        assert conn.execute(select(runs.c.status)).scalar_one() == 'failed'
    assert current['status'] == 'stopped' and current['stopped_at'] is not None
    assert task['last_success_at'] is None and task['status'] == 'failed'


def test_live_heartbeat_does_not_mean_ingestion_success(pg_engine):
    def fail(request):
        return httpx.Response(400)
    clock = Clock()
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        worker = Worker(pg_engine, settings(), clock, client=client)
        assert worker.tick()['outcome']['status'] == 'failed'
        clock.now += timedelta(seconds=10)
        worker.heartbeat()
        with pg_engine.connect() as conn:
            current = conn.execute(select(workers).where(workers.c.id == worker.id)).mappings().one()
            task = conn.execute(select(tasks).where(tasks.c.name == 'recent')).mappings().one()
        assert current['heartbeat_at'] == clock.now and current['status'] == 'running'
        assert task['last_success_at'] is None and task['status'] == 'failed'
        worker.close()


def test_worker_lost_lock_stops_before_data_write(pg_engine):
    from app.worker import session_lock as original_lock
    captured = []
    @contextmanager
    def capture(engine, identity):
        with original_lock(engine, identity) as guard:
            captured.append(guard)
            yield guard
    def lose_lock(request):
        with pg_engine.connect() as conn:
            conn.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': captured[0].pid})
        return httpx.Response(200, json=feed([feature(START)]))
    with httpx.Client(transport=httpx.MockTransport(lose_lock)) as client:
        worker = Worker(pg_engine, settings(), Clock(), client=client)
        with patch('app.worker.session_lock', capture), pytest.raises(OwnershipLost):
            worker.tick()
        worker.close()
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(earthquakes)).scalar_one() == 0
        assert conn.execute(select(runs.c.status)).scalar_one() == 'running'  # next lock owner recovers it


def test_explicit_deletion_revision_and_older_active_payload(pg_engine, source_run):
    original = normalize(feature(START))
    with pg_engine.begin() as conn:
        upsert_event(conn, original, utcnow(), source_run)
        deleted = dict(original, review_status='deleted',
                       source_updated_at=original['source_updated_at'] + timedelta(seconds=1))
        upsert_event(conn, deleted, utcnow(), source_run)
        upsert_event(conn, original, utcnow(), source_run)
    with pg_engine.connect() as conn:
        assert conn.execute(select(earthquakes.c.source_state)).scalar_one() == 'deleted'
        assert conn.execute(select(func.count()).select_from(revision_history)).scalar_one() == 1
