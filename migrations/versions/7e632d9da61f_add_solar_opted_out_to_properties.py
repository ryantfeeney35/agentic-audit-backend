"""add solar_opted_out to properties

Revision ID: 7e632d9da61f
Revises: f8e7d6c5b4a3
Create Date: 2026-03-23 23:43:42.096022

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7e632d9da61f'
down_revision = 'f8e7d6c5b4a3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.add_column(sa.Column('solar_opted_out', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade():
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.drop_column('solar_opted_out')
