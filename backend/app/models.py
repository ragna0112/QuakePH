"""SQLAlchemy tables shared by ingestion and Alembic metadata discovery."""
from sqlalchemy import Column, DateTime, Float, Integer, String, Table, Text, CheckConstraint
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
