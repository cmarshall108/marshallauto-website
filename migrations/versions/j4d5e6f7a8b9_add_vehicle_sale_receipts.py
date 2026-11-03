"""Add itemized private sale receipts."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing

revision = 'j4d5e6f7a8b9'
down_revision = 'i3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'vehicle_sale_receipts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'),
            nullable=False, unique=True,
        ),
        sa.Column('buyer_name', sa.String(128), nullable=True),
        sa.Column('buyer_address', sa.Text(), nullable=True),
        sa.Column('vehicle_price', sa.Numeric(10, 2), nullable=False),
        sa.Column('highway_use_tax', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('document_fee', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('title_registration_fee', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('credits', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('amount_received', sa.Numeric(10, 2), nullable=False),
        sa.Column('payment_method', sa.String(32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    create_table_if_missing(
        'vehicle_sale_receipt_charges',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'receipt_id', sa.Integer(), sa.ForeignKey('vehicle_sale_receipts.id'),
            nullable=False,
        ),
        sa.Column('description', sa.String(128), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
    )
    create_index_if_missing(
        'ix_vehicle_sale_receipt_charges_receipt_id',
        'vehicle_sale_receipt_charges',
        ['receipt_id'],
    )


def downgrade():
    op.drop_table('vehicle_sale_receipt_charges')
    op.drop_table('vehicle_sale_receipts')
