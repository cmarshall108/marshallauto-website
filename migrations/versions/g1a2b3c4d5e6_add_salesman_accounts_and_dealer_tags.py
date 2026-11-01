"""Add salesman profile fields and dealer tags for quick-select.

Revision ID: g1a2b3c4d5e6
Revises: f8a9b0c1d2e3
Create Date: 2026-10-31

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing, create_table_if_missing


revision = 'g1a2b3c4d5e6'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'user', sa.Column('role', sa.String(length=20), nullable=False, server_default='admin'),
    )
    add_column_if_missing('user', sa.Column('full_name', sa.String(length=128), nullable=True))
    add_column_if_missing('user', sa.Column('email', sa.String(length=120), nullable=True))
    add_column_if_missing('user', sa.Column('phone', sa.String(length=32), nullable=True))
    add_column_if_missing('user', sa.Column('dealer_tag', sa.String(length=64), nullable=True))
    add_column_if_missing(
        'user',
        sa.Column('profile_complete', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    add_column_if_missing(
        'user',
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    create_index_if_missing('ix_user_role', 'user', ['role'])

    create_table_if_missing(
        'dealer_tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('name'),
    )
    create_index_if_missing('ix_dealer_tags_name', 'dealer_tags', ['name'])
    create_index_if_missing('ix_dealer_tags_user_id', 'dealer_tags', ['user_id'])

    # Sale details can record which salesman closed the deal.
    add_column_if_missing('vehicles', sa.Column('salesperson', sa.String(length=128), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('salesperson')
    op.drop_table('dealer_tags')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index('ix_user_role')
        batch_op.drop_column('must_change_password')
        batch_op.drop_column('profile_complete')
        batch_op.drop_column('dealer_tag')
        batch_op.drop_column('phone')
        batch_op.drop_column('email')
        batch_op.drop_column('full_name')
        batch_op.drop_column('role')
