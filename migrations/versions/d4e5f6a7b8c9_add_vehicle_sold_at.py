"""add vehicle sold_at (tracks when a listing sold, for days-on-lot analytics)

Revision ID: d4e5f6a7b8c9
Revises: c8d9e0f1a2b3
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('sold_at', sa.DateTime(), nullable=True))
    create_index_if_missing('ix_vehicles_sold_at', 'vehicles', ['sold_at'])


def downgrade():
    op.drop_index('ix_vehicles_sold_at', table_name='vehicles')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('sold_at')
