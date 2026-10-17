"""Add vehicle acquisition costs and rebuild expense entries."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import (
    add_column_if_missing, create_index_if_missing, create_table_if_missing,
)

revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'vehicles',
        sa.Column('acquisition_cost', sa.Numeric(10, 2), nullable=True),
    )
    create_table_if_missing(
        'rebuild_cost_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'vehicle_id', sa.Integer(),
            sa.ForeignKey('vehicles.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('category', sa.String(24), nullable=False),
        sa.Column('description', sa.String(180), nullable=False),
        sa.Column('vendor', sa.String(128), nullable=True),
        sa.Column('purchase_date', sa.Date(), nullable=True),
        sa.Column('quantity', sa.Numeric(10, 2), nullable=False),
        sa.Column('unit_cost', sa.Numeric(10, 2), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    create_index_if_missing(
        'ix_rebuild_cost_entries_vehicle_id',
        'rebuild_cost_entries',
        ['vehicle_id'],
    )


def downgrade():
    op.drop_index(
        'ix_rebuild_cost_entries_vehicle_id',
        table_name='rebuild_cost_entries',
    )
    op.drop_table('rebuild_cost_entries')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('acquisition_cost')
