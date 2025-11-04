"""add audit_memory_embeddings table for semantic recall

Revision ID: 1a2b3c4d5e6f
Revises: 0f123456abcd
Create Date: 2025-11-03 00:05:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = '0f123456abcd'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Create table if it doesn't exist
    if not inspector.has_table('audit_memory_embeddings'):
        op.create_table(
            'audit_memory_embeddings',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('audit_id', sa.Integer(), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            # store embedding as JSONB (list of floats) for portability
            sa.Column('embedding', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        )

    # Create index if not exists
    op.execute('CREATE INDEX IF NOT EXISTS ix_audit_memory_embeddings_audit_id ON audit_memory_embeddings (audit_id)')


def downgrade():
    # Drop index and table if they exist
    op.execute('DROP INDEX IF EXISTS ix_audit_memory_embeddings_audit_id')
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table('audit_memory_embeddings'):
        op.drop_table('audit_memory_embeddings')
