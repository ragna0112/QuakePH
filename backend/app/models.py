"""SQLAlchemy tables shared by ingestion and Alembic metadata discovery."""
from sqlalchemy import Column, DateTime, Float, Integer, String, Table, Text, CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.database import Base


earthquakes = Table(
    'earthquakes', Base.metadata,
    Column('usgs_id', Text, primary_key=True),
    Column('event_time', DateTime(timezone=True), nullable=False),
    Column('source_updated_at', DateTime(timezone=True), nullable=False),
    Column('magnitude', Float), Column('magnitude_type', Text), Column('place', Text),
    Column('longitude', Float, nullable=False), Column('latitude', Float, nullable=False),
    Column('depth_km', Float, nullable=False),
    Column('review_status', Text), Column('event_type', Text, nullable=False),
    Column('source_url', Text),
    Column('first_seen_at', DateTime(timezone=True), nullable=False),
    Column('last_seen_at', DateTime(timezone=True), nullable=False),
    Column('raw_event', JSONB, nullable=False),
    CheckConstraint('longitude BETWEEN -180 AND 180', name='earthquakes_longitude_range'),
    CheckConstraint('latitude BETWEEN -90 AND 90', name='earthquakes_latitude_range'),
)

pipeline_runs = Table(
    'pipeline_runs', Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('ingestion_type', String(40), nullable=False),
    Column('started_at', DateTime(timezone=True), nullable=False),
    Column('ended_at', DateTime(timezone=True)),
    Column('status', String(20), nullable=False),
    Column('source', Text, nullable=False), Column('scope', JSONB, nullable=False),
    *[Column(name, Integer, nullable=False, server_default='0') for name in (
        'fetched', 'in_scope', 'inserted', 'updated', 'unchanged', 'rejected',
        'out_of_scope', 'excluded_type', 'failed_request_attempts')],
    Column('errors', JSONB, nullable=False, server_default='[]'),
    CheckConstraint("status IN ('running', 'success', 'partial', 'failed')", name='pipeline_runs_status'),
)

# Jobs describe requested work; windows are durable checkpoints; pipeline_runs
# retain each individual window attempt, including superseded/failed attempts.
import_jobs = Table(
    'import_jobs', Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('start_at', DateTime(timezone=True), nullable=False),
    Column('end_at', DateTime(timezone=True), nullable=False),
    Column('scope', JSONB, nullable=False), Column('settings', JSONB, nullable=False),
    Column('status', String(20), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('started_at', DateTime(timezone=True)), Column('ended_at', DateTime(timezone=True)),
    Column('executions', Integer, nullable=False, server_default='0'),
    Column('owner_token', UUID(as_uuid=True)), Column('last_error', JSONB),
    CheckConstraint('start_at < end_at', name='import_jobs_range'),
    CheckConstraint("status IN ('pending', 'running', 'success', 'failed')", name='import_jobs_status'),
)

import_windows = Table(
    'import_windows', Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('job_id', UUID(as_uuid=True), ForeignKey('import_jobs.id'), nullable=False, index=True),
    Column('parent_id', UUID(as_uuid=True), ForeignKey('import_windows.id')),
    Column('start_at', DateTime(timezone=True), nullable=False),
    Column('end_at', DateTime(timezone=True), nullable=False),
    Column('status', String(20), nullable=False),
    Column('attempts', Integer, nullable=False, server_default='0'),
    Column('started_at', DateTime(timezone=True)), Column('ended_at', DateTime(timezone=True)),
    Column('counters', JSONB, nullable=False, server_default='{}'), Column('last_error', JSONB),
    UniqueConstraint('job_id', 'start_at', 'end_at', name='import_windows_job_range'),
    CheckConstraint('start_at < end_at', name='import_windows_range'),
    CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'split')", name='import_windows_status'),
)

pipeline_runs.append_column(Column('import_window_id', UUID(as_uuid=True),
                                   ForeignKey('import_windows.id', name='fk_pipeline_runs_import_window'), index=True))


earthquakes.append_column(Column('coverage_status', String(20), nullable=False, server_default='inside'))
earthquakes.append_column(Column('coverage_scope', JSONB))
earthquakes.append_column(Column('source_state', String(20), nullable=False, server_default='active'))
earthquakes.append_column(Column('source_checked_at', DateTime(timezone=True)))

revision_history = Table(
    'revision_history', Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('usgs_id', Text, ForeignKey('earthquakes.usgs_id'), nullable=False, index=True),
    Column('previous_updated_at', DateTime(timezone=True), nullable=False),
    Column('new_updated_at', DateTime(timezone=True), nullable=False),
    Column('changes', JSONB, nullable=False),
    Column('pipeline_run_id', UUID(as_uuid=True), ForeignKey('pipeline_runs.id'), nullable=False),
    Column('recorded_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('usgs_id', 'new_updated_at', name='revision_history_event_version'),
)

reconciliations = Table(
    'reconciliations', Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('historical_job_id', UUID(as_uuid=True), ForeignKey('import_jobs.id'), nullable=False, unique=True),
    Column('start_at', DateTime(timezone=True), nullable=False),
    Column('end_at', DateTime(timezone=True), nullable=False),
    Column('scope', JSONB, nullable=False), Column('settings', JSONB, nullable=False),
    Column('status', String(20), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('ended_at', DateTime(timezone=True)), Column('owner_token', UUID(as_uuid=True)),
    Column('last_error', JSONB),
)

reconciliation_events = Table(
    'reconciliation_events', Base.metadata,
    Column('reconciliation_id', UUID(as_uuid=True), ForeignKey('reconciliations.id'), primary_key=True),
    Column('usgs_id', Text, ForeignKey('earthquakes.usgs_id'), primary_key=True),
    Column('status', String(30), nullable=False, server_default='pending'),
    Column('attempts', Integer, nullable=False, server_default='0'),
    Column('checked_at', DateTime(timezone=True)), Column('related_id', Text),
    Column('pipeline_run_id', UUID(as_uuid=True), ForeignKey('pipeline_runs.id')),
    Column('counters', JSONB, nullable=False, server_default='{}'), Column('last_error', JSONB),
)

workers = Table(
    'workers', Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('hostname', Text, nullable=False), Column('pid', Integer, nullable=False),
    Column('started_at', DateTime(timezone=True), nullable=False),
    Column('heartbeat_at', DateTime(timezone=True), nullable=False),
    Column('status', String(20), nullable=False), Column('activity', Text, nullable=False),
    Column('last_error', JSONB), Column('stopped_at', DateTime(timezone=True)),
)

scheduled_tasks = Table(
    'scheduled_tasks', Base.metadata,
    Column('name', String(30), primary_key=True),
    Column('interval_seconds', Float, nullable=False), Column('next_run_at', DateTime(timezone=True), nullable=False),
    Column('status', String(20), nullable=False),
    Column('worker_id', UUID(as_uuid=True), ForeignKey('workers.id')), Column('owner_token', UUID(as_uuid=True)),
    Column('last_attempt_at', DateTime(timezone=True)), Column('last_success_at', DateTime(timezone=True)),
    Column('last_attempt_id', UUID(as_uuid=True)), Column('last_success_id', UUID(as_uuid=True)),
    Column('reconciliation_id', UUID(as_uuid=True), ForeignKey('reconciliations.id')),
    Column('attempts', Integer, nullable=False, server_default='0'), Column('last_error', JSONB),
)


# Dashboard query patterns: filtered chronological pages, newest run pages,
# and latest completed ID observations (used by list/map/detail projections).
from sqlalchemy import Index
Index('ix_earthquakes_time_id', earthquakes.c.event_time.desc(), earthquakes.c.usgs_id)
Index('ix_pipeline_runs_started_id', pipeline_runs.c.started_at.desc(), pipeline_runs.c.id.desc())
Index('ix_reconciliation_events_latest', reconciliation_events.c.usgs_id,
      reconciliation_events.c.checked_at.desc(), reconciliation_events.c.reconciliation_id.desc())
