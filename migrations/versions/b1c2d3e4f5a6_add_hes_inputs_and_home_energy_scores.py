"""add hes_inputs to audits and home_energy_scores table

Revision ID: b1c2d3e4f5a6
Revises: a872c3419a2e
Create Date: 2026-06-24 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a6'
down_revision = 'a872c3419a2e'
branch_labels = None
depends_on = None

# Cross-database JSON: JSONB on PostgreSQL (prod), JSON on SQLite (dev/tests).
JSONB = sa.JSON().with_variant(PG_JSONB, 'postgresql')


def upgrade():
    op.add_column('audits', sa.Column('hes_inputs', JSONB, nullable=True))

    op.create_table(
        'home_energy_scores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('audit_id', sa.Integer(),
                  sa.ForeignKey('audits.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'),
        sa.Column('hescore_building_id', sa.String(length=64), nullable=True),
        sa.Column('assessment_type', sa.String(length=20), nullable=True),
        sa.Column('hpxml', sa.Text(), nullable=True),
        sa.Column('base_score', sa.Integer(), nullable=True),
        sa.Column('label_url', sa.String(), nullable=True),
        sa.Column('raw_result', JSONB, nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_home_energy_scores_audit_id', 'home_energy_scores', ['audit_id'])


def downgrade():
    op.drop_index('ix_home_energy_scores_audit_id', table_name='home_energy_scores')
    op.drop_table('home_energy_scores')
    op.drop_column('audits', 'hes_inputs')
