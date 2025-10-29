"""add source column to audit_recommendations

Revision ID: 999999999999
Revises: e92f11d87229_added_recommendations_table
Create Date: 2025-10-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '999999999999'
down_revision = 'c3d4e5f67890'
branch_labels = None
depends_on = None


def upgrade():
    # Add a non-nullable source column with default 'ai' for backwards compatibility.
    op.add_column(
        'audit_recommendations',
        sa.Column('source', sa.String(), nullable=False, server_default=sa.text("'ai'")),
    )

    # Ensure any existing NULLs are set to 'ai' (defensive)
    op.execute("UPDATE audit_recommendations SET source = 'ai' WHERE source IS NULL")


def downgrade():
    op.drop_column('audit_recommendations', 'source')
