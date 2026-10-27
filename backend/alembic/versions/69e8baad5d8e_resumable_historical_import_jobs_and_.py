"""resumable historical import jobs and windows

Revision ID: 69e8baad5d8e
Revises: 1522abd3414e
Create Date: 2026-10-28 00:21:44.915258

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '69e8baad5d8e'
down_revision: Union[str, Sequence[str], None] = '1522abd3414e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('import_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('executions', sa.Integer(), server_default='0', nullable=False),
    sa.Column('owner_token', sa.UUID(), nullable=True),
    sa.Column('last_error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'running', 'success', 'failed')", name='import_jobs_status'),
    sa.CheckConstraint('start_at < end_at', name='import_jobs_range'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('import_windows',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('counters', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('last_error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'split')", name='import_windows_status'),
    sa.CheckConstraint('start_at < end_at', name='import_windows_range'),
    sa.ForeignKeyConstraint(['job_id'], ['import_jobs.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['import_windows.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'start_at', 'end_at', name='import_windows_job_range')
    )
    op.create_index(op.f('ix_import_windows_job_id'), 'import_windows', ['job_id'], unique=False)
    op.add_column('pipeline_runs', sa.Column('import_window_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_pipeline_runs_import_window_id'), 'pipeline_runs', ['import_window_id'], unique=False)
    op.create_foreign_key('fk_pipeline_runs_import_window', 'pipeline_runs', 'import_windows', ['import_window_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_pipeline_runs_import_window', 'pipeline_runs', type_='foreignkey')
    op.drop_index(op.f('ix_pipeline_runs_import_window_id'), table_name='pipeline_runs')
    op.drop_column('pipeline_runs', 'import_window_id')
    op.drop_index(op.f('ix_import_windows_job_id'), table_name='import_windows')
    op.drop_table('import_windows')
    op.drop_table('import_jobs')
