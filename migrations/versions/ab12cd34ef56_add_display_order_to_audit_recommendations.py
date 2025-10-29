"""add display_order to audit_recommendations

Revision ID: ab12cd34ef56
Revises: e92f11d87229
Create Date: 2025-10-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ab12cd34ef56'
down_revision = 'e92f11d87229'
branch_labels = None
depends_on = None


def upgrade():
    # Add nullable integer column for custom display ordering
    op.add_column('audit_recommendations', sa.Column('display_order', sa.Integer(), nullable=True))
    # Add an index to speed up ordering queries
    op.create_index('ix_audit_recommendations_audit_id_display_order', 'audit_recommendations', ['audit_id', 'display_order'])


def downgrade():
    op.drop_index('ix_audit_recommendations_audit_id_display_order', table_name='audit_recommendations')
    op.drop_column('audit_recommendations', 'display_order')
