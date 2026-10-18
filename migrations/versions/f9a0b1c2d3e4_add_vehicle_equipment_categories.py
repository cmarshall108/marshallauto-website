"""add vehicle equipment categories

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing


revision = 'f9a0b1c2d3e4'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        'exterior_features',
        'interior_features',
        'entertainment_features',
        'mechanical_features',
        'safety_features',
    ):
        add_column_if_missing('vehicles', sa.Column(column, sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('safety_features')
        batch_op.drop_column('mechanical_features')
        batch_op.drop_column('entertainment_features')
        batch_op.drop_column('interior_features')
        batch_op.drop_column('exterior_features')
