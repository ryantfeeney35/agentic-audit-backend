"""Add room_measurements table for SLAM scans

Revision ID: 0e4d4a193efa
Revises: 9c10e5250efa
Create Date: 2026-02-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '0e4d4a193efa'
down_revision = '9c10e5250efa'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'room_measurements',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('audit_id', sa.Integer(), sa.ForeignKey('audits.id', ondelete='CASCADE'), nullable=False),
        sa.Column('room_id', sa.String(length=64), nullable=False),
        sa.Column('area_sqft', sa.Float(), nullable=False),
        sa.Column('polygon_vertices', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column('quality_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('user_modified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('user_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_room_measurements_audit_room_created',
        'room_measurements',
        ['audit_id', 'room_id', 'created_at'],
        unique=False
    )


def downgrade():
    op.drop_index('ix_room_measurements_audit_room_created', table_name='room_measurements')
    op.drop_table('room_measurements')
