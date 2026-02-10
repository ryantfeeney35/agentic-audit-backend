"""Rename room_id to audit_step_id with FK cascade delete

Revision ID: g1h2i3j4k5l6
Revises: 0e4d4a193efa
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g1h2i3j4k5l6'
down_revision = '0e4d4a193efa'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Drop the existing index that uses room_id
    op.drop_index('ix_room_measurements_audit_room_created', table_name='room_measurements')
    
    # 2. Rename column from room_id to audit_step_id
    op.alter_column('room_measurements', 'room_id', new_column_name='audit_step_id')
    
    # 3. Convert audit_step_id from varchar to integer (existing values should be numeric strings)
    op.execute("""
        ALTER TABLE room_measurements 
        ALTER COLUMN audit_step_id TYPE INTEGER USING audit_step_id::integer
    """)
    
    # 4. Delete orphaned room_measurements that reference non-existent audit_steps
    op.execute("""
        DELETE FROM room_measurements 
        WHERE audit_step_id NOT IN (SELECT id FROM audit_steps)
    """)
    
    # 5. Add foreign key constraint with cascade delete
    op.create_foreign_key(
        'fk_room_measurements_audit_step',
        'room_measurements',
        'audit_steps',
        ['audit_step_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    # 6. Recreate the index with the new column name
    op.create_index(
        'ix_room_measurements_audit_step_created',
        'room_measurements',
        ['audit_id', 'audit_step_id', 'created_at'],
        unique=False
    )


def downgrade():
    # 1. Drop the index
    op.drop_index('ix_room_measurements_audit_step_created', table_name='room_measurements')
    
    # 2. Drop the foreign key constraint
    op.drop_constraint('fk_room_measurements_audit_step', 'room_measurements', type_='foreignkey')
    
    # 3. Convert audit_step_id back to varchar
    op.execute("""
        ALTER TABLE room_measurements 
        ALTER COLUMN audit_step_id TYPE VARCHAR(64) USING audit_step_id::text
    """)
    
    # 4. Rename column back to room_id
    op.alter_column('room_measurements', 'audit_step_id', new_column_name='room_id')
    
    # 5. Recreate the original index
    op.create_index(
        'ix_room_measurements_audit_room_created',
        'room_measurements',
        ['audit_id', 'room_id', 'created_at'],
        unique=False
    )
