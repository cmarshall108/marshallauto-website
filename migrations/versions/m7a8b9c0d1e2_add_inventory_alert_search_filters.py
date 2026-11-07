"""Add filter fields for inventory search alerts."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing

revision = 'm7a8b9c0d1e2'
down_revision = 'l6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'inventory_alerts',
        sa.Column('min_price', sa.Numeric(10, 2), nullable=True),
    )
    add_column_if_missing(
        'inventory_alerts',
        sa.Column('max_mileage', sa.Integer(), nullable=True),
    )
    add_column_if_missing(
        'inventory_alerts',
        sa.Column('title_status', sa.String(20), nullable=True),
    )
    add_column_if_missing(
        'inventory_alerts',
        sa.Column('search_query', sa.String(128), nullable=True),
    )


def downgrade():
    with op.batch_alter_table('inventory_alerts', schema=None) as batch_op:
        batch_op.drop_column('search_query')
        batch_op.drop_column('title_status')
        batch_op.drop_column('max_mileage')
        batch_op.drop_column('min_price')
