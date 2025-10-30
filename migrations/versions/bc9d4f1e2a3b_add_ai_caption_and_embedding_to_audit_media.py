"""add ai_caption and ai_embedding to audit_media

Revision ID: bc9d4f1e2a3b
Revises: a1b2c3d4e5f6
Create Date: 2025-10-29 23:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bc9d4f1e2a3b'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('audit_media', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_caption', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('ai_embedding', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('audit_media', schema=None) as batch_op:
        batch_op.drop_column('ai_embedding')
        batch_op.drop_column('ai_caption')
