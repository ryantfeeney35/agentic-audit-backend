"""add is_hidden to audit_recommendations

Revision ID: b7f3c9a8d2e1
Revises: merge_112409ab12
Create Date: 2025-10-28 13:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7f3c9a8d2e1'
down_revision = 'merge_112409ab12'
branch_labels = None
depends_on = None


def upgrade():
    # Add `is_hidden` column with default false for Postgres.
    op.add_column('audit_recommendations', sa.Column('is_hidden', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade():
    op.drop_column('audit_recommendations', 'is_hidden')
