"""add detailed vehicle specifications

Revision ID: e8f9a0b1c2d3
Revises: d3e4f5a6b7c8
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing


revision = 'e8f9a0b1c2d3'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('mpg_combined', sa.Integer(), nullable=True))
    add_column_if_missing('vehicles', sa.Column('doors', sa.Integer(), nullable=True))
    add_column_if_missing('vehicles', sa.Column('passenger_capacity', sa.Integer(), nullable=True))
    add_column_if_missing('vehicles', sa.Column('gvwr', sa.String(length=64), nullable=True))
    add_column_if_missing(
        'vehicles', sa.Column('additional_specifications', sa.Text(), nullable=True),
    )


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('additional_specifications')
        batch_op.drop_column('gvwr')
        batch_op.drop_column('passenger_capacity')
        batch_op.drop_column('doors')
        batch_op.drop_column('mpg_combined')
