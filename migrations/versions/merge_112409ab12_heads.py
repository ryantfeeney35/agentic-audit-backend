"""Merge heads 112409eb2c4f and ab12cd34ef56

Revision ID: merge_112409ab12
Revises: 112409eb2c4f, ab12cd34ef56
Create Date: 2025-10-28 12:45:00.000000

This is a no-op merge revision to unify divergent migration heads.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'merge_112409ab12'
down_revision = ('112409eb2c4f', 'ab12cd34ef56')
branch_labels = None
depends_on = None


def upgrade():
    # Merge-only revision: no schema changes.
    pass


def downgrade():
    # No downgrade for merge-only revision.
    pass
