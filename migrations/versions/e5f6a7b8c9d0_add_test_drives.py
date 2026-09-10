"""add test_drives table (dealer test-drive checkout/return tracking)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import has_table


# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    if has_table('test_drives'):
        return
    op.create_table(
        'test_drives',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('customer_name', sa.String(length=128), nullable=False),
        sa.Column('customer_phone', sa.String(length=32), nullable=True),
        sa.Column('customer_email', sa.String(length=120), nullable=True),
        sa.Column('license_number', sa.String(length=64), nullable=True),
        sa.Column('license_state', sa.String(length=32), nullable=True),
        sa.Column('license_image_filename', sa.String(length=256), nullable=True),
        sa.Column('salesperson', sa.String(length=128), nullable=True),
        sa.Column('start_mileage', sa.Integer(), nullable=True),
        sa.Column('end_mileage', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('returned_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='out'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_test_drives_vehicle_id', 'test_drives', ['vehicle_id'])
    op.create_index('ix_test_drives_status', 'test_drives', ['status'])


def downgrade():
    op.drop_index('ix_test_drives_status', table_name='test_drives')
    op.drop_index('ix_test_drives_vehicle_id', table_name='test_drives')
    op.drop_table('test_drives')
