"""add extracted driver's-license details to test drives

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing


revision = 'a1b2c3d4e5f6'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('test_drives', sa.Column('license_date_of_birth', sa.Date(), nullable=True))
    add_column_if_missing('test_drives', sa.Column('license_expiration_date', sa.Date(), nullable=True))
    add_column_if_missing('test_drives', sa.Column('license_address', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('test_drives', schema=None) as batch_op:
        batch_op.drop_column('license_address')
        batch_op.drop_column('license_expiration_date')
        batch_op.drop_column('license_date_of_birth')