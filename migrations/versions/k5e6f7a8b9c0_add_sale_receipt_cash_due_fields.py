"""Add salesman, sales tax, fees, and cash-due fields to sale receipts."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, has_column, has_table

revision = 'k5e6f7a8b9c0'
down_revision = 'j4d5e6f7a8b9'
branch_labels = None
depends_on = None

_TABLE = 'vehicle_sale_receipts'


def upgrade():
    add_column_if_missing(_TABLE, sa.Column('salesman', sa.String(128), nullable=True))
    add_column_if_missing(
        _TABLE, sa.Column('sales_tax', sa.Numeric(10, 2), nullable=False, server_default='0'),
    )
    add_column_if_missing(
        _TABLE, sa.Column('warranty_fee', sa.Numeric(10, 2), nullable=False, server_default='0'),
    )
    add_column_if_missing(
        _TABLE, sa.Column('tags_fee', sa.Numeric(10, 2), nullable=False, server_default='0'),
    )
    add_column_if_missing(
        _TABLE, sa.Column('down_payment', sa.Numeric(10, 2), nullable=False, server_default='0'),
    )
    add_column_if_missing(
        _TABLE, sa.Column('trade_allow', sa.Numeric(10, 2), nullable=False, server_default='0'),
    )
    if not has_table(_TABLE):
        return
    if has_column(_TABLE, 'highway_use_tax') and has_column(_TABLE, 'sales_tax'):
        op.execute(sa.text(
            'UPDATE vehicle_sale_receipts '
            'SET sales_tax = highway_use_tax '
            'WHERE sales_tax = 0 AND highway_use_tax <> 0'
        ))
    if has_column(_TABLE, 'title_registration_fee') and has_column(_TABLE, 'tags_fee'):
        op.execute(sa.text(
            'UPDATE vehicle_sale_receipts '
            'SET tags_fee = title_registration_fee '
            'WHERE tags_fee = 0 AND title_registration_fee <> 0'
        ))
    if has_column(_TABLE, 'credits') and has_column(_TABLE, 'down_payment'):
        op.execute(sa.text(
            'UPDATE vehicle_sale_receipts '
            'SET down_payment = credits '
            'WHERE down_payment = 0 AND credits <> 0'
        ))


def downgrade():
    if not has_table(_TABLE):
        return
    names = [
        name for name in (
            'trade_allow', 'down_payment', 'tags_fee', 'warranty_fee', 'sales_tax', 'salesman',
        )
        if has_column(_TABLE, name)
    ]
    if not names:
        return
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        for name in names:
            batch_op.drop_column(name)
