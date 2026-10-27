"""Manual catalog backfills with durable, atomic window checkpoints."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import re
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import get_engine
from app.ingestion import (COUNTERS, COVERAGE, RequestFailure, in_bounds, normalize,
                           request_usgs, upsert_event, utcnow, validate_feed)
from app.models import import_jobs as jobs, import_windows as windows, pipeline_runs as runs

CATALOG = 'https://earthquake.usgs.gov/fdsnws/event/1/'
DEFAULT_SETTINGS = dict(version=1, source=CATALOG, window_days=7,
                        result_limit=20000, minimum_window_ms=1, orderby='time-asc',
                        eventtype='earthquake', timeout_seconds=30, connect_timeout_seconds=10)


class JobBusy(RuntimeError):
    pass


class OwnershipLost(RuntimeError):
    pass


def parse_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise argparse.ArgumentTypeError('use YYYY-MM-DD (UTC midnight)')
    try:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('invalid calendar date') from exc


def create_job(engine, start, end, settings, window_days=7):
    if start.tzinfo != timezone.utc or end.tzinfo != timezone.utc or start >= end:
        raise ValueError('require UTC start < exclusive end')
    if not 1 <= window_days <= 31:
        raise ValueError('window-days must be between 1 and 31')
    job_id = uuid4()
    scope = {key: getattr(settings, key) for key in (
        'min_latitude', 'max_latitude', 'min_longitude', 'max_longitude')}
    scope['label'] = COVERAGE
    configuration = DEFAULT_SETTINGS | {'window_days': window_days}
    with engine.begin() as connection:
        connection.execute(jobs.insert().values(id=job_id, start_at=start, end_at=end,
            scope=scope, settings=configuration, status='pending', created_at=utcnow()))
        cursor = start
        while cursor < end:
            # Avoid datetime overflow near the last representable date.
            boundary = cursor + min(timedelta(days=window_days), end - cursor)
            connection.execute(windows.insert().values(id=uuid4(), job_id=job_id,
                start_at=cursor, end_at=boundary, status='pending', counters={}))
            cursor = boundary
    return job_id


@contextmanager
def job_lock(engine, job_id):
    # Transaction-scoped advisory lock releases on disconnect/process death.
    # This connection holds only the lock; checkpoint transactions use the pool.
    key = int.from_bytes(job_id.bytes[:8], 'big', signed=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if not connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}).scalar_one():
                raise JobBusy(f'Job {job_id} is already being executed; no work was changed')
            yield
        finally:
            transaction.rollback()


@contextmanager
def owned_transaction(engine, job_id, token):
    # Fencing protects against an old worker whose lock connection disappeared
    # while an HTTP request was in flight. Lock acquisition alone cannot do that.
    with engine.begin() as connection:
        owner = connection.execute(select(jobs.c.owner_token).where(jobs.c.id == job_id)
                                   .with_for_update()).scalar_one()
        if owner != token:
            raise OwnershipLost('Another executor owns this job; stale worker stopped')
        yield connection


def report(engine, job_id):
    with engine.connect() as connection:
        job = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
        work = connection.execute(select(windows).where(windows.c.job_id == job_id)).mappings().all()
        failed_requests = connection.execute(select(func.coalesce(func.sum(runs.c.failed_request_attempts), 0))
            .select_from(runs.join(windows, runs.c.import_window_id == windows.c.id))
            .where(windows.c.job_id == job_id)).scalar_one()
    progress = dict.fromkeys(('pending', 'running', 'completed', 'failed', 'split'), 0)
    counters = dict.fromkeys(COUNTERS, 0)
    counters['outside_time'] = 0
    for window in work:
        progress[window['status']] += 1
        if window['status'] == 'completed':
            for key in counters:
                counters[key] += window['counters'].get(key, 0)
    return dict(job_id=str(job_id), status=job['status'], start=job['start_at'].isoformat(),
                exclusive_end=job['end_at'].isoformat(), executions=job['executions'],
                windows=progress, window_attempts=sum(w['attempts'] for w in work),
                total_failed_request_attempts=failed_requests,
                committed_counters=counters, last_error=job['last_error'])


def params_for(window, job):
    scope, config = job['scope'], job['settings']
    return dict(starttime=window['start_at'].isoformat(timespec='milliseconds'),
                endtime=window['end_at'].isoformat(timespec='milliseconds'),
                minlatitude=scope['min_latitude'], maxlatitude=scope['max_latitude'],
                minlongitude=scope['min_longitude'], maxlongitude=scope['max_longitude'],
                eventtype=config['eventtype'])


def fetch_count(client, job, params, failed, sleep):
    response = request_usgs(client, job['settings']['source'] + 'count', failed, sleep,
                            params=params | {'format': 'text'})
    if not re.fullmatch(r'\d+', response.text.strip()):
        raise ValueError('invalid catalog count response; expected nonnegative integer')
    return int(response.text.strip())


def fetch_window(client, window, job, failed, sleep):
    params = params_for(window, job)
    limit = job['settings']['result_limit']
    count = fetch_count(client, job, params, failed, sleep)
    if count >= limit:
        return None, count
    try:
        response = request_usgs(client, job['settings']['source'] + 'query', failed, sleep,
            params=params | {'format': 'geojson', 'limit': limit, 'orderby': job['settings']['orderby']})
    except RequestFailure as exc:
        # Count/query can race with catalog revisions. Only interpret HTTP 400 as
        # overflow when a fresh count confirms saturation; don't split bad queries.
        if exc.status_code == 400:
            count = fetch_count(client, job, params, failed, sleep)
            if count >= limit:
                return None, count
        raise
    if response.status_code == 204:
        return [], 0
    # Catalog responses can omit metadata.count (observed on empty queries).
    # When supplied it must match; preflight and saturation checks still apply.
    features = validate_feed(response.json(), require_count=False)
    if len(features) >= limit:
        return None, len(features)
    return features, count


def recover(engine, job_id, token):
    """Only the new lock owner can classify earlier unfinished work abandoned."""
    with owned_transaction(engine, job_id, token) as connection:
        abandoned = connection.execute(select(windows.c.id).where(
            windows.c.job_id == job_id, windows.c.status == 'running')).scalars().all()
        for window_id in abandoned:
            message = {'kind': 'interrupted', 'detail': 'Previous executor released its job lock before checkpoint completion'}
            connection.execute(windows.update().where(windows.c.id == window_id).values(
                status='failed', ended_at=utcnow(), last_error=message))
            previous_runs = connection.execute(select(runs).where(
                runs.c.import_window_id == window_id, runs.c.status == 'running')).mappings().all()
            for previous in previous_runs:
                connection.execute(runs.update().where(runs.c.id == previous['id']).values(
                    status='failed', ended_at=utcnow(), errors=previous['errors'] + [message]))


def attempt_window(engine, job, window, token, client, sleep):
    job_id, window_id, run_id = job['id'], window['id'], uuid4()
    counts = dict.fromkeys(COUNTERS, 0)
    counts['outside_time'] = 0
    errors = []
    scope = job['scope'] | dict(job_id=str(job_id), start=window['start_at'].isoformat(),
                              exclusive_end=window['end_at'].isoformat())
    with owned_transaction(engine, job_id, token) as connection:
        connection.execute(windows.update().where(windows.c.id == window_id).values(
            status='running', attempts=windows.c.attempts + 1, started_at=utcnow(),
            ended_at=None, last_error=None, counters={}))
        connection.execute(runs.insert().values(id=run_id, import_window_id=window_id,
            ingestion_type='historical_window', started_at=utcnow(), status='running',
            source=job['settings']['source'], scope=scope, errors=[]))

    def failed(detail):
        counts['failed_request_attempts'] += 1
        errors.append(detail)
        with owned_transaction(engine, job_id, token) as connection:
            connection.execute(runs.update().where(runs.c.id == run_id).values(
                failed_request_attempts=counts['failed_request_attempts'], errors=errors))

    try:
        features, catalog_count = fetch_window(client, window, job, failed, sleep)
        with owned_transaction(engine, job_id, token) as connection:
            if features is None:
                units = (window['end_at'] - window['start_at']) // timedelta(milliseconds=1)
                minimum = job['settings']['minimum_window_ms']
                if units < 2 * minimum:
                    raise ValueError(f'Minimum {minimum}ms window reached result limit; cannot certify completeness ({catalog_count} results)')
                midpoint = window['start_at'] + timedelta(milliseconds=units // 2)
                for start, end in [(window['start_at'], midpoint), (midpoint, window['end_at'])]:
                    connection.execute(windows.insert().values(id=uuid4(), job_id=job_id,
                        parent_id=window_id, start_at=start, end_at=end, status='pending', counters={}))
                disposition = 'split'
            else:
                counts['fetched'] = len(features)
                accepted = []
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
                    elif not in_bounds(event, SimpleNamespace(**job['scope'])):
                        counts['out_of_scope'] += 1
                    elif not window['start_at'] <= event['event_time'] < window['end_at']:
                        counts['outside_time'] += 1
                    else:
                        counts['in_scope'] += 1
                        accepted.append(event)
                # API sorts time ascending; stable local tie ordering avoids relying
                # on unspecified server ordering of events at the same millisecond.
                for event in sorted(accepted, key=lambda e: (e['event_time'], e['usgs_id'], e['source_updated_at'])):
                    counts[upsert_event(connection, event, utcnow())] += 1
                if counts['rejected']:
                    raise ValueError('Rejected records prevent a complete checkpoint; window data rolled back')
                disposition = 'completed'
            connection.execute(windows.update().where(windows.c.id == window_id).values(
                status=disposition, ended_at=utcnow(), counters=counts | {'catalog_count': catalog_count}, last_error=None))
            connection.execute(runs.update().where(runs.c.id == run_id).values(
                **{k: counts[k] for k in COUNTERS}, status='success', ended_at=utcnow(), errors=errors,
                scope=scope | {'disposition': disposition, 'catalog_count': catalog_count,
                               'outside_time': counts['outside_time']}))
        return True
    except OwnershipLost:
        raise
    except Exception as exc:
        counts.update(inserted=0, updated=0, unchanged=0)
        message = {'kind': 'window', 'reason': type(exc).__name__,
                   'detail': str(exc)[:500] if isinstance(exc, (ValueError, RuntimeError))
                   else 'Window failed; data transaction rolled back. Check PostgreSQL/schema or response encoding.'}
        errors.append(message)
        with owned_transaction(engine, job_id, token) as connection:
            connection.execute(windows.update().where(windows.c.id == window_id).values(
                status='failed', ended_at=utcnow(), counters=counts, last_error=message))
            connection.execute(runs.update().where(runs.c.id == run_id).values(
                **{k: counts[k] for k in COUNTERS}, status='failed', ended_at=utcnow(), errors=errors))
            connection.execute(jobs.update().where(jobs.c.id == job_id).values(
                status='failed', ended_at=utcnow(), last_error=message))
        return False


def execute_job(engine, job_id, client=None, sleep=time.sleep):
    job_id = UUID(str(job_id))
    with job_lock(engine, job_id):
        token = uuid4()
        with engine.begin() as connection:
            job = connection.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().one_or_none()
            if job is None:
                raise ValueError(f'Unknown import job {job_id}')
            if job['settings']['version'] != 1:
                raise ValueError('Unsupported saved job settings version')
            if job['status'] == 'success':
                return report(engine, job_id)
            connection.execute(jobs.update().where(jobs.c.id == job_id).values(
                owner_token=token, status='running', executions=jobs.c.executions + 1,
                started_at=job['started_at'] or utcnow(), ended_at=None, last_error=None))
        recover(engine, job_id, token)
        if client is None:
            with httpx.Client(timeout=httpx.Timeout(job['settings']['timeout_seconds'],
                connect=job['settings']['connect_timeout_seconds']), follow_redirects=True,
                headers={'User-Agent': 'QuakePH-student-project/0.3'}) as own_client:
                run_windows(engine, job, token, own_client, sleep)
        else:
            run_windows(engine, job, token, client, sleep)
        return report(engine, job_id)


def run_windows(engine, job, token, client, sleep):
    while True:
        with owned_transaction(engine, job['id'], token) as connection:
            window = connection.execute(select(windows).where(windows.c.job_id == job['id'],
                windows.c.status.in_(['pending', 'failed'])).order_by(windows.c.start_at, windows.c.end_at)).mappings().first()
            if window is None:
                unfinished = connection.execute(select(windows.c.id).where(
                    windows.c.job_id == job['id'], windows.c.status.not_in(['completed', 'split']))).first()
                if unfinished:
                    raise RuntimeError('Job has unfinished work and cannot be marked successful')
                connection.execute(jobs.update().where(jobs.c.id == job['id']).values(
                    status='success', ended_at=utcnow(), last_error=None))
                return
        if not attempt_window(engine, job, window, token, client, sleep):
            return


def main():
    parser = argparse.ArgumentParser(description='USGS historical imports; date-only inputs are UTC midnight; end is exclusive.')
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('import')
    start.add_argument('--start', required=True, type=parse_date)
    start.add_argument('--end', required=True, type=parse_date)
    start.add_argument('--window-days', type=int, default=7, choices=range(1, 32))
    resume = commands.add_parser('resume')
    resume.add_argument('job_id', type=UUID)
    args = parser.parse_args()
    if args.command == 'import' and args.start >= args.end:
        parser.error('--start must be before exclusive --end')
    job_id = None
    try:
        engine = get_engine()
        job_id = (create_job(engine, args.start, args.end, get_settings(), args.window_days)
                  if args.command == 'import' else args.job_id)
        # Flush the durable job identifier before doing network work, for Ctrl+C/crash recovery.
        print(json.dumps({'job_id': str(job_id), 'message': 'Resume with this ID if interrupted'}), flush=True)
        result = execute_job(engine, job_id)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'success' else 1
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ValueError, JobBusy, OwnershipLost)) else 'Could not execute/finalize job; check PostgreSQL and migrations, then resume the job ID.'
        print(json.dumps({'job_id': str(job_id) if job_id else None, 'status': 'incomplete', 'reason': type(exc).__name__, 'detail': detail}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
