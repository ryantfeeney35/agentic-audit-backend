"""add subscription metadata to utility_connections

Revision ID: a1b2c3d4e5f6
Revises: e5718d0d5413, 7e632d9da61f
Create Date: 2026-07-12 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = ('e5718d0d5413', '7e632d9da61f')
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('utility_connections', sa.Column('obfuscated_key', sa.String(length=255), nullable=True))
    op.add_column('utility_connections', sa.Column('meter_number', sa.String(length=100), nullable=True))
    op.add_column('utility_connections', sa.Column('rate_tariff', sa.String(length=100), nullable=True))
    op.add_column('utility_connections', sa.Column('account_group', sa.String(length=100), nullable=True))
    op.add_column('utility_connections', sa.Column('last_file_received_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('utility_connections', 'last_file_received_at')
    op.drop_column('utility_connections', 'account_group')
    op.drop_column('utility_connections', 'rate_tariff')
    op.drop_column('utility_connections', 'meter_number')
    op.drop_column('utility_connections', 'obfuscated_key')
