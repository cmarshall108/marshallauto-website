"""Enforce a single active financing application per vehicle.

Revision ID: i3c4d5e6f7a8
Revises: h2b3c4d5e6f7
Create Date: 2026-11-02

"""
import sqlalchemy as sa
from alembic import op

from migrations.compat import create_index_if_missing, has_index, has_table

revision = 'i3c4d5e6f7a8'
down_revision = 'h2b3c4d5e6f7'
branch_labels = None
depends_on = None


_INDEX_NAME = 'uq_financing_applications_vehicle_id'
_TABLE_NAME = 'financing_applications'


def upgrade():
    if has_table(_TABLE_NAME):
        op.execute(sa.text(
            'UPDATE financing_applications '
            'SET vehicle_id = NULL '
            'WHERE vehicle_id IS NOT NULL '
            'AND EXISTS ('
            '  SELECT 1 FROM financing_applications AS newer '
            '  WHERE newer.vehicle_id = financing_applications.vehicle_id '
            '  AND newer.id > financing_applications.id'
            ')',
        ))
        create_index_if_missing(
            _INDEX_NAME, _TABLE_NAME, ['vehicle_id'], unique=True,
        )


def downgrade():
    if has_index(_TABLE_NAME, _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE_NAME)
