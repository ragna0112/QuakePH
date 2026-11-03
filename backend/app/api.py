"""Read-only dashboard queries; no imports, scheduler startup, or USGS requests."""
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, cast, func, select, text, case
from sqlalchemy.engine import Connection

from app import api_models as m
from app.config import get_settings
from app.database import get_engine
from app.models import (earthquakes as eq, revision_history as revisions, pipeline_runs as runs,
                        import_windows as windows, reconciliations as reconciles,
                        reconciliation_events as observations, workers, scheduled_tasks as tasks)

router = APIRouter(prefix='/api', tags=['Dashboard'], responses={503: {'description': 'Database unavailable; no internal connection details returned.'}})
MANILA = ZoneInfo('Asia/Manila')
REVISION_LIMIT = 20
WORKER_LIMIT = 10
FAILURE_LIMIT = 10
ERROR_LIMIT = 3
TERMINAL_OBSERVATIONS = ('found', 'missing', 'deleted', 'redirected', 'unresolved')


def utcnow():
    return datetime.now(timezone.utc)


def read_connection():
    # One snapshot for counts, rows, charts, and metadata. READ ONLY is enforced by
    # PostgreSQL, not merely a convention in these handlers. Pool settings reset on exit.
    with get_engine().connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        with conn.begin():
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            yield conn


Read = Annotated[Connection, Depends(read_connection)]


def coverage():
    settings = get_settings()
    return m.Coverage(**{key: getattr(settings, key) for key in
        ('min_latitude', 'max_latitude', 'min_longitude', 'max_longitude')})


def filters_only(query):
    return m.Filters(**{key: getattr(query, key) for key in m.Filters.model_fields})


def predicates(query):
    """Identical scientific/source/geographic semantics for list, map, and stats."""
    result = [eq.c.event_type == 'earthquake']
    if not query.include_deleted:
        result.append(eq.c.source_state != 'deleted')
    if not query.include_outside:
        scope = coverage()
        result += [eq.c.latitude.between(scope.min_latitude, scope.max_latitude),
                   eq.c.longitude.between(scope.min_longitude, scope.max_longitude)]
    if query.start is not None:
        result.append(eq.c.event_time >= query.start)
    if query.end is not None:
        result.append(eq.c.event_time < query.end)
    for field, lower, upper in [(eq.c.magnitude, query.min_magnitude, query.max_magnitude),
                                 (eq.c.depth_km, query.min_depth, query.max_depth)]:
        if lower is not None:
            result.append(field >= lower)
        if upper is not None:
            result.append(field <= upper)
    if query.place is not None:
        result.append(eq.c.place.icontains(query.place, autoescape=True))
    return result


def ordering(sort):
    # No user-controlled SQL identifiers or fragments.
    allowed = {'time_desc': eq.c.event_time.desc(), 'time_asc': eq.c.event_time.asc(),
        'magnitude_desc': eq.c.magnitude.desc().nulls_last(),
        'magnitude_asc': eq.c.magnitude.asc().nulls_last(), 'depth_asc': eq.c.depth_km.asc().nulls_last(),
        'depth_desc': eq.c.depth_km.desc().nulls_last(), 'updated_desc': eq.c.source_updated_at.desc()}
    return allowed[sort], eq.c.usgs_id.asc()


def event_query():
    last_observation = (select(observations.c.status).where(observations.c.usgs_id == eq.c.usgs_id,
        observations.c.status.in_(TERMINAL_OBSERVATIONS), observations.c.checked_at.is_not(None))
        .order_by(observations.c.checked_at.desc(), observations.c.reconciliation_id.desc())
        .limit(1).correlate(eq).scalar_subquery())
    # Explicit projection excludes raw_event even for detail responses.
    return select(*(column for column in eq.c if column.name != 'raw_event'),
                  last_observation.label('last_source_observation'))


def summary(row):
    if row is None:
        return None
    scope = coverage()
    inside = (scope.min_latitude <= row['latitude'] <= scope.max_latitude
              and scope.min_longitude <= row['longitude'] <= scope.max_longitude)
    values = {key: row[key] for key in m.EventSummary.model_fields if key != 'coverage_status'}
    return m.EventSummary(**values, coverage_status='inside' if inside else 'outside')


def pagination(query, total):
    return m.Pagination(page=query.page, page_size=query.page_size,
        total_pages=(total + query.page_size - 1)//query.page_size,
        has_next=query.page * query.page_size < total, has_previous=query.page > 1 and total > 0)


@router.get('/earthquakes', response_model=m.EventList, summary='List stored earthquakes with shared filters')
def earthquake_list(query: Annotated[m.ListQuery, Query()], conn: Read):
    where = predicates(query)
    total = conn.execute(select(func.count()).select_from(eq).where(*where)).scalar_one()
    rows = conn.execute(event_query().where(*where).order_by(*ordering(query.sort))
        .offset((query.page - 1)*query.page_size).limit(query.page_size)).mappings().all()
    return m.EventList(items=[summary(row) for row in rows], total=total, filters=filters_only(query),
                       coverage=coverage(), pagination=pagination(query, total), sort=query.sort)


# Register static /map before the parameter route below.
@router.get('/earthquakes/map', response_model=m.MapData, summary='Limited GeoJSON from the same earthquake filters',
            description='Newest-first, ID ascending for ties. Defaults to 1000 features; maximum 5000. Metadata explicitly reports truncation.')
def earthquake_map(query: Annotated[m.MapQuery, Query()], conn: Read):
    where = predicates(query)
    total = conn.execute(select(func.count()).select_from(eq).where(*where)).scalar_one()
    rows = conn.execute(event_query().where(*where).order_by(*ordering('time_desc')).limit(query.limit)).mappings().all()
    features = []
    for row in rows:
        event = summary(row)
        features.append(m.Feature(id=event.usgs_id,
            geometry=m.Geometry(coordinates=(event.longitude, event.latitude)),
            properties=m.MapProperties(**{key: getattr(event, key) for key in m.MapProperties.model_fields})))
    return m.MapData(features=features, metadata=m.MapMetadata(total=total, returned=len(features),
        limit=query.limit, truncated=total > len(features), filters=filters_only(query), coverage=coverage()))


def public_url(value):
    if not value:
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme == 'https' and parts.hostname == 'earthquake.usgs.gov'
                and parts.username is None and parts.password is None and parts.port in (None, 443)
                and not parts.query and not parts.fragment):
            return value
    except ValueError:
        pass
    return None


@router.get('/earthquakes/{event_id}', response_model=m.EventDetail, summary='Look up any stored event, including moved/deleted events',
            description='Direct lookup bypasses coverage/source filtering. Revision history is limited to the newest 20 accepted revisions.')
def earthquake_detail(event_id: str, conn: Read):
    row = conn.execute(event_query().where(eq.c.usgs_id == event_id)).mappings().first()
    if row is None:
        raise HTTPException(404, 'Earthquake not found')
    total = conn.execute(select(func.count()).select_from(revisions).where(revisions.c.usgs_id == event_id)).scalar_one()
    history = conn.execute(select(revisions).where(revisions.c.usgs_id == event_id)
        .order_by(revisions.c.new_updated_at.desc(), revisions.c.id.desc()).limit(REVISION_LIMIT)).mappings().all()
    items = []
    tracked = {'event_time', 'magnitude', 'magnitude_type', 'place', 'longitude', 'latitude',
               'depth_km', 'review_status', 'event_type', 'source_url', 'source_state'}
    for item in history:
        changes = {key: value for key, value in item['changes'].items() if key in tracked}
        if 'source_url' in changes:
            changes['source_url'] = {key: public_url(changes['source_url'].get(key)) for key in ('old', 'new')}
        items.append(m.Revision(**{key: item[key] for key in m.Revision.model_fields if key != 'changes'}, changes=changes))
    return m.EventDetail(**summary(row).model_dump(), source_url=public_url(row['source_url']),
        first_seen_at=row['first_seen_at'], last_seen_at=row['last_seen_at'], coverage=coverage(),
        recorded_coverage_status=row['coverage_status'],
        source_status_note='Source state is last known authoritative status. Missing/unresolved checks do not prove deletion; no records are deleted by this API.',
        revisions=m.RevisionPage(items=items, total=total, returned=len(items), limit=REVISION_LIMIT, truncated=total > len(items)))


@router.get('/stats', response_model=m.Statistics, summary='Full matching totals and a bounded Manila daily chart',
    description='Count/strongest/latest cover ALL matching rows. Daily chart defaults to at most 30 days, maximum 366, and explicitly reports matching events outside its date window. Zero never verifies complete imports.')
def statistics(query: Annotated[m.StatsQuery, Query()], conn: Read):
    where = predicates(query)
    total = conn.execute(select(func.count()).select_from(eq).where(*where)).scalar_one()
    latest = conn.execute(event_query().where(*where).order_by(*ordering('time_desc')).limit(1)).mappings().first()
    strongest = conn.execute(event_query().where(*where, eq.c.magnitude.is_not(None))
        .order_by(eq.c.magnitude.desc(), eq.c.event_time.desc(), eq.c.usgs_id.asc()).limit(1)).mappings().first()
    try:
        anchor = query.end - timedelta(microseconds=1) if query.end else latest['event_time'] if latest else utcnow()
        if not query.end and not latest and query.start:
            anchor = max(anchor, query.start)
        last_day = anchor.astimezone(MANILA).date()
        first_day = last_day - timedelta(days=query.daily_days - 1)
        if query.start:
            first_day = max(first_day, query.start.astimezone(MANILA).date())
        chart_start = datetime.combine(first_day, datetime.min.time(), MANILA).astimezone(timezone.utc)
        chart_end = datetime.combine(last_day + timedelta(days=1), datetime.min.time(), MANILA).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise HTTPException(422, 'Chart dates exceed the supported calendar range')
    day = cast(func.timezone('Asia/Manila', eq.c.event_time), Date)
    counts = dict(conn.execute(select(day, func.count()).where(*where,
        eq.c.event_time >= chart_start, eq.c.event_time < chart_end).group_by(day)).all())
    days = [m.DailyCount(day=first_day + timedelta(days=index), count=counts.get(first_day + timedelta(days=index), 0))
            for index in range((last_day - first_day).days + 1)]
    chart_total = sum(item.count for item in days)
    return m.Statistics(matching_count=total, strongest=summary(strongest), latest=summary(latest),
        filters=filters_only(query), coverage=coverage(), daily=m.DailyChart(start_day=first_day, end_day=last_day,
            days=days, events_in_chart=chart_total, events_outside_chart=total-chart_total, truncated=total > chart_total))


ERROR_REASONS = {'ValueError', 'RuntimeError', 'RequestFailure', 'OperationalError', 'DataError',
    'IntegrityError', 'ConnectError', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout',
    'HTTPStatusError', 'RemoteProtocolError', 'StopRequested', 'OwnershipLost', 'JSONDecodeError',
    'interrupted', 'task_incomplete', 'shutdown_requested'}
ERROR_MESSAGES = {'request': 'USGS request attempt failed.', 'record': 'Source record failed validation.',
    'run': 'Ingestion attempt did not complete.', 'window': 'Historical window failed; its data writes rolled back.',
    'recheck': 'Stored-ID recheck did not complete.', 'interrupted': 'Earlier owner released its lock before completion.',
    'task': 'Scheduled task failed or is incomplete.', 'other': 'A failure was recorded; inspect the associated attempt.'}


def safe_error(value):
    if not isinstance(value, dict):
        return m.SafeError(kind='other', reason=None, message=ERROR_MESSAGES['other'])
    supplied_kind, supplied_reason = value.get('kind'), value.get('reason')
    reason = supplied_reason if isinstance(supplied_reason, str) and supplied_reason in ERROR_REASONS else None
    kind = supplied_kind if isinstance(supplied_kind, str) and supplied_kind in ERROR_MESSAGES else 'task' if reason else 'other'
    result = dict(kind=kind, reason=reason, message=ERROR_MESSAGES[kind])
    for source, target, lower, upper in [('http_status', 'http_status', 100, 599), ('attempt', 'attempt', 1, 100), ('index', 'record_index', 0, 1000000000)]:
        number = value.get(source)
        if type(number) is int and lower <= number <= upper:
            result[target] = number
    return m.SafeError(**result)


def safe_scope(scope, start=None, end=None):
    scope = scope if isinstance(scope, dict) else {}
    bounds = None
    keys = ('min_latitude', 'max_latitude', 'min_longitude', 'max_longitude')
    if all(type(scope.get(key)) in (float, int) and math.isfinite(scope[key]) for key in keys):
        bounds = m.Coverage(**{key: scope[key] for key in keys})
    def instant(value):
        try:
            parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
            return parsed.astimezone(timezone.utc) if isinstance(parsed, datetime) and parsed.tzinfo else None
        except (ValueError, OverflowError):
            return None
    event_id = scope.get('event_id')
    return m.RunScope(coverage=bounds, start=instant(start or scope.get('start')),
        exclusive_end=instant(end or scope.get('exclusive_end')),
        event_id=event_id if isinstance(event_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', event_id) else None)


def run_query():
    return select(*runs.c, windows.c.job_id.label('import_job_id'),
        reconciles.c.id.label('catalog_reconciliation_id')).select_from(runs.outerjoin(windows,
        runs.c.import_window_id == windows.c.id).outerjoin(reconciles, reconciles.c.historical_job_id == windows.c.job_id))


def run_summary(row):
    errors = row['errors'] if isinstance(row['errors'], list) else []
    reconciliation_id = row['catalog_reconciliation_id']
    if reconciliation_id is None:
        try:
            reconciliation_id = UUID(str(row['scope'].get('reconciliation_id')))
        except (ValueError, AttributeError):
            pass
    return m.RunSummary(id=row['id'], run_type=row['ingestion_type'] if row['ingestion_type'] in m.RunType.__args__ else 'other',
        status=row['status'] if row['status'] in m.RunStatus.__args__ else 'unknown',
        started_at=row['started_at'], ended_at=row['ended_at'], counters=m.Counters(**{key: row[key] for key in m.Counters.model_fields}),
        scope=safe_scope(row['scope']), import_window_id=row['import_window_id'], import_job_id=row['import_job_id'],
        reconciliation_id=reconciliation_id, errors=m.ErrorPage(items=[safe_error(error) for error in errors[:ERROR_LIMIT]],
        recorded_count=len(errors), truncated=len(errors) > ERROR_LIMIT))


@router.get('/pipeline/runs', response_model=m.RunList, summary='Paginated pipeline attempts, not aggregate job totals')
def pipeline_runs(query: Annotated[m.RunsQuery, Query()], conn: Read):
    where = []
    if query.run_type:
        where.append(runs.c.ingestion_type == query.run_type)
    if query.status:
        where.append(runs.c.status == query.status)
    total = conn.execute(select(func.count()).select_from(runs).where(*where)).scalar_one()
    rows = conn.execute(run_query().where(*where).order_by(runs.c.started_at.desc(), runs.c.id.desc())
        .offset((query.page-1)*query.page_size).limit(query.page_size)).mappings().all()
    return m.RunList(items=[run_summary(row) for row in rows], total=total,
        pagination=pagination(query, total), run_type=query.run_type, status=query.status)


def scheduled_lock_held(conn):
    # Mirror coordination.session_lock's schema namespace/hash, WITHOUT acquiring a lock.
    namespace = conn.get_execution_options().get('schema_translate_map', {}).get(None, 'public')
    key = int.from_bytes(hashlib.sha256(f'{namespace}:quakeph:scheduled-ingestion:v1'.encode()).digest()[:8], 'big')
    return conn.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory'
        AND classid=:high AND objid=:low AND objsubid=1 AND granted
        AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))"""),
        {'high': key >> 32, 'low': key & ((1 << 32)-1)}).scalar_one()


@router.get('/pipeline/status', response_model=m.PipelineStatus, summary='Persisted worker liveness, schedules, and failures; never triggers collection')
def pipeline_status(conn: Read):
    now = utcnow()
    freshness = get_settings().api_worker_fresh_seconds
    fresh = (workers.c.status == 'running') & (workers.c.heartbeat_at >= now-timedelta(seconds=freshness)) & (workers.c.heartbeat_at <= now)
    total = conn.execute(select(func.count()).select_from(workers)).scalar_one()
    alive = conn.execute(select(func.count()).select_from(workers).where(fresh)).scalar_one()
    running = conn.execute(select(func.count()).select_from(workers).where(workers.c.status == 'running')).scalar_one()
    rows = conn.execute(select(workers).order_by(case((fresh, 0), (workers.c.status == 'running', 1), else_=2),
        workers.c.heartbeat_at.desc(), workers.c.id.desc()).limit(WORKER_LIMIT)).mappings().all()
    states = []
    for row in rows:
        age = (now-row['heartbeat_at']).total_seconds()
        states.append(m.WorkerState(id=row['id'], recorded_status=row['status'] if row['status'] in ('running', 'stopped', 'failed') else 'unknown',
            recorded_activity=row['activity'] if row['activity'] in ('recent', 'reconcile', 'idle', 'standby', 'stopped') else 'unknown',
            started_at=row['started_at'], heartbeat_at=row['heartbeat_at'], heartbeat_age_seconds=age,
            heartbeat_fresh=0 <= age <= freshness, stopped_at=row['stopped_at'],
            error=safe_error(row['last_error']) if row['last_error'] else None))
    task_states = []
    for row in conn.execute(select(tasks).where(tasks.c.name.in_(['recent', 'reconcile'])).order_by(tasks.c.name)).mappings():
        scope = None
        if row['name'] == 'reconcile' and row['last_attempt_id']:
            rec = conn.execute(select(reconciles).where(reconciles.c.id == row['last_attempt_id'])).mappings().first()
            if rec:
                scope = safe_scope(rec['scope'], rec['start_at'], rec['end_at'])
        values = {key: row[key] for key in ('name', 'worker_id', 'interval_seconds', 'last_attempt_at', 'last_success_at',
            'last_attempt_id', 'last_success_id', 'next_run_at', 'reconciliation_id')}
        task_states.append(m.TaskState(**values, recorded_status=row['status'] if row['status'] in ('pending', 'running', 'success', 'failed') else 'unknown',
            recorded_running=row['status'] == 'running', overdue=row['next_run_at'] <= now,
            reconciliation_scope=scope, error=safe_error(row['last_error']) if row['last_error'] else None))
    failures = conn.execute(run_query().where(runs.c.status.in_(['failed', 'partial']))
        .order_by(runs.c.started_at.desc(), runs.c.id.desc()).limit(FAILURE_LIMIT+1)).mappings().all()
    return m.PipelineStatus(observed_at=now, worker_state='not_observed' if total == 0 else 'alive' if alive else 'stale' if running else 'stopped',
        freshness_seconds=freshness, workers=states, workers_total=total, workers_truncated=total > len(states),
        scheduled_lock_held=scheduled_lock_held(conn), tasks=task_states, recent_failures=[run_summary(row) for row in failures[:FAILURE_LIMIT]],
        failures_limit=FAILURE_LIMIT, failures_truncated=len(failures) > FAILURE_LIMIT)
