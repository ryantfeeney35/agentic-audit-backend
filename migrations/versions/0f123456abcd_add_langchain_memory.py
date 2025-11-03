"""add langchain_memory table for persistent chat history

Revision ID: 0f123456abcd
Revises: 999999999999
Create Date: 2025-11-03 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0f123456abcd'
down_revision = 'bc9d4f1e2a3b'
branch_labels = None
depends_on = None


def upgrade():
     op.create_table(
         'langchain_memory',
         sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
         sa.Column('session_id', sa.Text(), nullable=False),
         sa.Column('audit_id', sa.Integer(), nullable=True),
         sa.Column('role', sa.Text(), nullable=False),
         sa.Column('content', sa.Text(), nullable=False),
         sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
         sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
     )
     op.create_index('ix_langchain_memory_session_id', 'langchain_memory', ['session_id'])
     op.create_index('ix_langchain_memory_audit_id', 'langchain_memory', ['audit_id'])


def downgrade():
     op.drop_index('ix_langchain_memory_audit_id', table_name='langchain_memory')
     op.drop_index('ix_langchain_memory_session_id', table_name='langchain_memory')
     op.drop_table('langchain_memory')
