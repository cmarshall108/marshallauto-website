"""Add public hold requests and inventory alerts.

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a7
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing

revision = 'c3d4e5f6a7b8'
down_revision = 'b1c2d3e4f5a7'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'vehicle_hold_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('customer_name', sa.String(128), nullable=False),
        sa.Column('customer_email', sa.String(120), nullable=False),
        sa.Column('customer_phone', sa.String(32), nullable=True),
        sa.Column('deposit_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='requested'),
        sa.Column('terms_acknowledged_at', sa.DateTime(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    create_index_if_missing('ix_vehicle_hold_requests_vehicle_id', 'vehicle_hold_requests', ['vehicle_id'])
    create_index_if_missing('ix_vehicle_hold_requests_lead_id', 'vehicle_hold_requests', ['lead_id'])
    create_index_if_missing('ix_vehicle_hold_requests_status', 'vehicle_hold_requests', ['status'])

    create_table_if_missing(
        'inventory_alerts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=True),
        sa.Column('customer_name', sa.String(128), nullable=False),
        sa.Column('customer_email', sa.String(120), nullable=False),
        sa.Column('customer_phone', sa.String(32), nullable=True),
        sa.Column('alert_type', sa.String(20), nullable=False),
        sa.Column('make', sa.String(64), nullable=True),
        sa.Column('body_style', sa.String(64), nullable=True),
        sa.Column('max_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('last_seen_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('last_notified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    create_index_if_missing('ix_inventory_alerts_lead_id', 'inventory_alerts', ['lead_id'])
    create_index_if_missing('ix_inventory_alerts_vehicle_id', 'inventory_alerts', ['vehicle_id'])
    create_index_if_missing('ix_inventory_alerts_customer_email', 'inventory_alerts', ['customer_email'])
    create_index_if_missing('ix_inventory_alerts_make', 'inventory_alerts', ['make'])
    create_index_if_missing('ix_inventory_alerts_body_style', 'inventory_alerts', ['body_style'])
    create_index_if_missing('ix_inventory_alerts_status', 'inventory_alerts', ['status'])


def downgrade():
    op.drop_table('inventory_alerts')
    op.drop_table('vehicle_hold_requests')
