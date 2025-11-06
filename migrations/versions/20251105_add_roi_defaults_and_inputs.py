"""
add roi_defaults to audits and roi_inputs to audit_recommendations

Revision ID: 20251105_add_roi
Revises: 
Create Date: 2025-11-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251105_add_roi'
down_revision = '1a2b3c4d5e6f'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('audits', sa.Column('roi_defaults', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")))
    op.add_column('audit_recommendations', sa.Column('roi_inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")))

    # drop server defaults after setting initial value
    op.alter_column('audits', 'roi_defaults', server_default=None)
    op.alter_column('audit_recommendations', 'roi_inputs', server_default=None)


def downgrade():
    op.drop_column('audit_recommendations', 'roi_inputs')
    op.drop_column('audits', 'roi_defaults')
