"""drop job tables for langgraph migration

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('artifacts')
    op.drop_table('job_steps')
    op.drop_index('ix_jobs_status_created', table_name='jobs')
    op.drop_table('jobs')

    # Drop enums that are no longer needed
    sa.Enum(name='artifacttype').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='stepstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='jobmode').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='jobstatus').drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    jobstatus_enum = sa.Enum('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELED', name='jobstatus')
    jobmode_enum = sa.Enum('FORM', 'AGENT', name='jobmode')
    stepstatus_enum = sa.Enum('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED', name='stepstatus')
    artifacttype_enum = sa.Enum('input', 'intermediate', 'output', name='artifacttype')

    jobstatus_enum.create(op.get_bind(), checkfirst=True)
    jobmode_enum.create(op.get_bind(), checkfirst=True)
    stepstatus_enum.create(op.get_bind(), checkfirst=True)
    artifacttype_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'jobs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('status', jobstatus_enum, nullable=False),
        sa.Column('mode', jobmode_enum, nullable=False),
        sa.Column('instruction', sa.Text, nullable=True),
        sa.Column('plan_json', JSON, nullable=False),
        sa.Column('progress', sa.Integer, nullable=False, server_default='0'),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('result_path', sa.String(1024), nullable=True),
        sa.Column('result_type', sa.String(32), nullable=True),
    )
    op.create_index('ix_jobs_status_created', 'jobs', ['status', 'created_at'])

    op.create_table(
        'job_steps',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', UUID(as_uuid=True), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('idx', sa.Integer, nullable=False),
        sa.Column('tool_name', sa.String(128), nullable=False),
        sa.Column('params_json', JSON, nullable=False),
        sa.Column('status', stepstatus_enum, nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('log_text', sa.Text, nullable=True),
        sa.Column('output_path', sa.String(1024), nullable=True),
    )

    op.create_table(
        'artifacts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', UUID(as_uuid=True), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('step_id', UUID(as_uuid=True), sa.ForeignKey('job_steps.id'), nullable=True),
        sa.Column('type', artifacttype_enum, nullable=False),
        sa.Column('path', sa.String(1024), nullable=False),
        sa.Column('meta_json', JSON, nullable=True),
    )
