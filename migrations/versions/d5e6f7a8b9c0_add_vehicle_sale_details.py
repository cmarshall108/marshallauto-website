"""Add private sold-vehicle details and buyer ID photos."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import (
    add_column_if_missing, create_index_if_missing, create_table_if_missing,
)

revision = 'd5e6f7a8b9c0'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('sold_price', sa.Numeric(10, 2), nullable=True))
    add_column_if_missing('vehicles', sa.Column('payment_method', sa.String(32), nullable=True))
    add_column_if_missing('vehicles', sa.Column('sale_notes', sa.Text(), nullable=True))
    create_table_if_missing(
        'vehicle_sale_images',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('filename', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    create_index_if_missing('ix_vehicle_sale_images_vehicle_id', 'vehicle_sale_images', ['vehicle_id'])


def downgrade():
    op.drop_table('vehicle_sale_images')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('sale_notes')
        batch_op.drop_column('payment_method')
        batch_op.drop_column('sold_price')
