"""Add private receipt photos to rebuild cost entries."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing


revision = 'd3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'rebuild_cost_receipts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'cost_entry_id', sa.Integer(),
            sa.ForeignKey('rebuild_cost_entries.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('filename', sa.String(256), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    create_index_if_missing(
        'ix_rebuild_cost_receipts_cost_entry_id',
        'rebuild_cost_receipts',
        ['cost_entry_id'],
    )


def downgrade():
    op.drop_index(
        'ix_rebuild_cost_receipts_cost_entry_id',
        table_name='rebuild_cost_receipts',
    )
    op.drop_table('rebuild_cost_receipts')
