"""add recommended_media_source to audit_recommendations

Revision ID: a1b2c3d4e5f6
Revises: 0d250575fc92
Create Date: 2025-10-29 22:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '0d250575fc92'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('audit_recommendations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recommended_media_source', sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table('audit_recommendations', schema=None) as batch_op:
        batch_op.drop_column('recommended_media_source')
