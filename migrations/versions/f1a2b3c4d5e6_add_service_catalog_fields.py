"""Add service catalog fields to audit_recommendations

Revision ID: f1a2b3c4d5e6
Revises: 
Create Date: 2026-01-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e5718d0d5413'  # Will be set during migration
branch_labels = None
depends_on = None


def upgrade():
    # Add service catalog alignment columns to audit_recommendations
    op.add_column('audit_recommendations', sa.Column('service_id', sa.String(100), nullable=True))
    op.add_column('audit_recommendations', sa.Column('order_of_completion', sa.Integer(), nullable=True))
    op.add_column('audit_recommendations', sa.Column('rebate_eligible', sa.Boolean(), nullable=True))
    
    # Create index on service_id for efficient lookups
    op.create_index('ix_audit_recommendations_service_id', 'audit_recommendations', ['service_id'])


def downgrade():
    # Remove index
    op.drop_index('ix_audit_recommendations_service_id', table_name='audit_recommendations')
    
    # Remove columns
    op.drop_column('audit_recommendations', 'rebate_eligible')
    op.drop_column('audit_recommendations', 'order_of_completion')
    op.drop_column('audit_recommendations', 'service_id')
