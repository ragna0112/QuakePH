"""Refresh catalog windows, then recheck a frozen set of stored IDs without a box filter."""
import argparse
from contextlib import nullcontext
import json
import time
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, literal

from app.config import get_settings
from app.database import get_engine
from app.coordination import (session_lock, transaction, fenced_by, OwnershipLost,
                              check_guards)
from app.historical import create_job, execute_job, report, parse_date, CATALOG
from app.ingestion import (COUNTERS, SOURCE_FIELDS, scope_values, normalize, timestamp,
                           upsert_event, request_usgs, RequestFailure, utcnow)
from app.models import (earthquakes, reconciliations as reconciles,
                        reconciliation_events as events, pipeline_runs as runs)

TERMINAL = ('found', 'missing', 'deleted', 'redirected', 'unresolved')


def create_reconciliation(engine, start, end, settings, connection=None):
    with (nullcontext(connection) if connection is not None else transaction(engine)) as conn:
        historical_id = create_job(engine, start, end, settings, connection=conn)
        identifier = uuid4()
        conn.execute(reconciles.insert().values(id=identifier, historical_job_id=historical_id,
            start_at=start, end_at=end, scope=scope_values(settings),
            settings={'version': 1, 'max_ids_per_pass': settings.recheck_max_ids},
            status='pending', created_at=utcnow()))
        # Freeze membership BEFORE refreshing origin times/locations in the catalog.
        conn.execute(events.insert().from_select(['reconciliation_id', 'usgs_id'],
            select(literal(identifier), earthquakes.c.usgs_id).where(
                earthquakes.c.event_time >= start, earthquakes.c.event_time < end)))
    return identifier


def result(engine, identifier):
    with engine.connect() as conn:
        job = conn.execute(select(reconciles).where(reconciles.c.id == identifier)).mappings().one()
        rows = conn.execute(select(events).where(events.c.reconciliation_id == identifier)).mappings().all()
    counts = dict.fromkeys(COUNTERS, 0)
    statuses = {}
    for row in rows:
        statuses[row['status']] = statuses.get(row['status'], 0) + 1
        if row['status'] in TERMINAL:
            for key in counts:
                counts[key] += row['counters'].get(key, 0)
    return dict(reconciliation_id=str(identifier), status=job['status'],
                start=job['start_at'].isoformat(), exclusive_end=job['end_at'].isoformat(),
                historical=report(engine, job['historical_job_id']),
                id_observations=statuses, recheck_counters=counts, last_error=job['last_error'])


def recheck(engine, job, item, client, sleep):
    run_id = uuid4()
    counts, errors = dict.fromkeys(COUNTERS, 0), []
    predicate = (events.c.reconciliation_id == job['id'], events.c.usgs_id == item['usgs_id'])
    with transaction(engine) as conn:
        # Only under the reconciliation lock can an older unfinished attempt be abandoned.
        if item['status'] == 'running' and item['pipeline_run_id']:
            previous = conn.execute(select(runs).where(runs.c.id == item['pipeline_run_id'])).mappings().one()
            conn.execute(runs.update().where(runs.c.id == previous['id']).values(status='failed',
                ended_at=utcnow(), errors=previous['errors'] + [{'kind': 'interrupted', 'reason': 'Owner lock released before observation committed'}]))
        conn.execute(runs.insert().values(id=run_id, ingestion_type='reconcile_event',
            source=CATALOG, scope=job['scope'] | {'reconciliation_id': str(job['id']), 'event_id': item['usgs_id']},
            started_at=utcnow(), status='running', errors=[]))
        conn.execute(events.update().where(*predicate).values(status='running',
            attempts=events.c.attempts + 1, pipeline_run_id=run_id, last_error=None))

    def failed(detail):
        counts['failed_request_attempts'] += 1
        errors.append(detail)
        with transaction(engine) as conn:
            conn.execute(runs.update().where(runs.c.id == run_id).values(
                failed_request_attempts=counts['failed_request_attempts'], errors=errors))

    try:
        try:
            response = request_usgs(client, CATALOG + 'query', failed, sleep, params=dict(
                format='geojson', eventid=item['usgs_id'], includedeleted='true', nodata=404))
            payload = None if response.status_code == 204 else response.json()
            observation = 'missing' if payload is None else 'found'
        except RequestFailure as exc:
            if exc.status_code not in (404, 409):
                raise
            payload = None
            observation = 'missing' if exc.status_code == 404 else 'unresolved'
        if payload is not None:
            if not isinstance(payload, dict) or payload.get('type') != 'Feature':
                raise ValueError('Event-ID query must return a GeoJSON Feature, not a collection')
            if not isinstance(payload.get('properties'), dict):
                raise ValueError('Event-ID properties must be an object')
        with transaction(engine) as conn:
            related_id = None
            if payload is not None:
                counts['fetched'] = 1
                if payload['properties'].get('status') == 'deleted':
                    if payload.get('id') != item['usgs_id']:
                        raise ValueError('Deleted response does not identify the requested event')
                    stored = conn.execute(select(earthquakes).where(
                        earthquakes.c.usgs_id == item['usgs_id']).with_for_update()).mappings().one()
                    # A deletion tombstone may lack geometry. Preserve last known values.
                    event = {key: stored[key] for key in SOURCE_FIELDS}
                    event.update(usgs_id=item['usgs_id'], raw_event=payload, review_status='deleted',
                                 source_state='deleted', source_updated_at=timestamp(payload['properties'].get('updated'), 'updated'))
                    json.dumps(payload, allow_nan=False)
                    observation = 'deleted'
                else:
                    event = normalize(payload)
                    observation = 'found' if event['usgs_id'] == item['usgs_id'] else 'redirected'
                    related_id = event['usgs_id'] if observation == 'redirected' else None
                # Do not filter by box or origin time here: detecting moves is the point.
                counts['in_scope'] = 1  # membership in the frozen ID scope, not current geography
                counts[upsert_event(conn, event, utcnow(), run_id, job['scope'])] += 1
            conn.execute(earthquakes.update().where(earthquakes.c.usgs_id == item['usgs_id']).values(source_checked_at=utcnow()))
            conn.execute(events.update().where(*predicate).values(status=observation, related_id=related_id,
                checked_at=utcnow(), counters=counts, last_error=None))
            conn.execute(runs.update().where(runs.c.id == run_id).values(**counts,
                status='success', ended_at=utcnow(), errors=errors))
        return True
    except OwnershipLost:
        raise
    except Exception as exc:
        counts.update(inserted=0, updated=0, unchanged=0)
        if isinstance(exc, ValueError):
            counts['rejected'] += 1
        error = {'kind': 'recheck', 'reason': type(exc).__name__,
                 'detail': str(exc)[:400] if isinstance(exc, (ValueError, RuntimeError)) else 'Recheck rolled back; check database or response encoding'}
        with transaction(engine, allow_stopping=True) as conn:
            conn.execute(events.update().where(*predicate).values(status='failed', counters=counts, last_error=error))
            conn.execute(runs.update().where(runs.c.id == run_id).values(**counts,
                status='failed', ended_at=utcnow(), errors=errors + [error]))
        return False


def execute_reconciliation(engine, identifier, client=None, sleep=time.sleep):
    identifier = UUID(str(identifier))
    with session_lock(engine, f'reconciliation:{identifier}'):
        token = uuid4()
        with transaction(engine) as conn:
            job = conn.execute(select(reconciles).where(reconciles.c.id == identifier).with_for_update()).mappings().one()
            if job['status'] == 'success':
                return result(engine, identifier)
            conn.execute(reconciles.update().where(reconciles.c.id == identifier).values(
                status='running', owner_token=token, ended_at=None, last_error=None))

        def fence(conn):
            owner = conn.execute(select(reconciles.c.owner_token).where(
                reconciles.c.id == identifier).with_for_update()).scalar_one()
            if owner != token:
                raise OwnershipLost('Reconciliation ownership changed')

        with fenced_by(fence):
            try:
                catalog = execute_job(engine, job['historical_job_id'], client, sleep)
                if catalog['status'] != 'success':
                    raise RuntimeError('Catalog stage incomplete; resume retains completed windows')
                with (nullcontext(client) if client is not None else httpx.Client(
                    timeout=httpx.Timeout(30, connect=10), follow_redirects=True,
                    headers={'User-Agent': 'QuakePH-student-project/0.4'})) as http:
                    for _ in range(job['settings']['max_ids_per_pass']):
                        check_guards()
                        with engine.connect() as conn:
                            item = conn.execute(select(events).where(events.c.reconciliation_id == identifier,
                                events.c.status.not_in(TERMINAL)).order_by(events.c.usgs_id).limit(1)).mappings().first()
                        if item is None:
                            break
                        if not recheck(engine, job, item, http, sleep):
                            raise RuntimeError('ID recheck failed; resume retains completed observations')
                with transaction(engine) as conn:
                    unfinished = conn.execute(select(events.c.usgs_id).where(events.c.reconciliation_id == identifier,
                        events.c.status.not_in(TERMINAL)).limit(1)).first()
                    conn.execute(reconciles.update().where(reconciles.c.id == identifier).values(
                        status='incomplete' if unfinished else 'success', ended_at=utcnow(),
                        last_error={'reason': 'ID budget reached; resume remaining observations'} if unfinished else None))
            except OwnershipLost:
                raise
            except Exception as exc:
                with transaction(engine, allow_stopping=True) as conn:
                    conn.execute(reconciles.update().where(reconciles.c.id == identifier).values(
                        status='failed', ended_at=utcnow(), last_error={'reason': type(exc).__name__,
                        'detail': str(exc)[:400] if isinstance(exc, RuntimeError) else 'Reconciliation interrupted; resume after database recovery'}))
        return result(engine, identifier)


def main():
    parser = argparse.ArgumentParser(description='Reconcile a UTC [start, end) range, including stored-ID rechecks.')
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('import')
    start.add_argument('--start', type=parse_date, required=True)
    start.add_argument('--end', type=parse_date, required=True)
    commands.add_parser('resume').add_argument('reconciliation_id', type=UUID)
    args = parser.parse_args()
    if args.command == 'import' and args.start >= args.end:
        parser.error('--start must precede exclusive --end')
    identifier = None
    try:
        engine = get_engine()
        identifier = (create_reconciliation(engine, args.start, args.end, get_settings())
                      if args.command == 'import' else args.reconciliation_id)
        print(json.dumps({'reconciliation_id': str(identifier)}), flush=True)
        outcome = execute_reconciliation(engine, identifier)
        print(json.dumps(outcome, indent=2))
        return 0 if outcome['status'] == 'success' else 1
    except Exception as exc:
        print(json.dumps({'reconciliation_id': str(identifier) if identifier else None,
            'status': 'incomplete', 'reason': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
