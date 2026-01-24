"""add_recommendation_type_and_user_status

Revision ID: c44270eacac6
Revises: 041c87ef64f3
Create Date: 2026-01-23 22:33:54.187914

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c44270eacac6'
down_revision = '041c87ef64f3'
branch_labels = None
depends_on = None


def upgrade():
    # Add recommendation_type column: 'upgrade' (default) or 'behavior'
    op.add_column('audit_recommendations', sa.Column(
        'recommendation_type', 
        sa.String(20), 
        nullable=False, 
        server_default='upgrade'
    ))
    
    # Add user_status column: 'interested', 'not_relevant', 'completed', or null
    op.add_column('audit_recommendations', sa.Column(
        'user_status', 
        sa.String(20), 
        nullable=True
    ))
    
    # Add user_status_updated_at column
    op.add_column('audit_recommendations', sa.Column(
        'user_status_updated_at', 
        sa.DateTime, 
        nullable=True
    ))


def downgrade():
    op.drop_column('audit_recommendations', 'user_status_updated_at')
    op.drop_column('audit_recommendations', 'user_status')
    op.drop_column('audit_recommendations', 'recommendation_type')
