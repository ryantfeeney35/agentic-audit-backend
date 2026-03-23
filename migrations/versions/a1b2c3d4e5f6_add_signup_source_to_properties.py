"""Add signup_source to properties

Revision ID: f8e7d6c5b4a3
Revises: ce9dac2b6026
Create Date: 2026-03-23

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8e7d6c5b4a3'
down_revision = 'ce9dac2b6026'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.add_column(sa.Column('signup_source', sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.drop_column('signup_source')
