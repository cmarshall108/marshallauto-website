"""Add advertised price-drop tracking.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing

revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('previous_price', sa.Numeric(10, 2), nullable=True))
    add_column_if_missing('vehicles', sa.Column('price_dropped_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('price_dropped_at')
        batch_op.drop_column('previous_price')
