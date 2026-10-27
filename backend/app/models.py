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
