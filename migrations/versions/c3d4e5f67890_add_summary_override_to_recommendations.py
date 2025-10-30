"""add summary_override to audit_recommendations

Revision ID: c3d4e5f67890
Revises: ab12cd34ef56
Create Date: 2025-10-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f67890'
down_revision = 'b7f3c9a8d2e1'
branch_labels = None
depends_on = None


def upgrade():
    # Add nullable text column for human-refined summaries
    op.add_column('audit_recommendations', sa.Column('summary_override', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('audit_recommendations', 'summary_override')
