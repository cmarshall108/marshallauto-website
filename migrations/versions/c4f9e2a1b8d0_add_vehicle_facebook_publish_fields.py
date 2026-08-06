"""add vehicle facebook publish fields

Revision ID: c4f9e2a1b8d0
Revises: a8be384dab1e
Create Date: 2026-03-22

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, has_column, has_table


# revision identifiers, used by Alembic.
revision = 'c4f9e2a1b8d0'
down_revision = 'a8be384dab1e'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('facebook_post_id', sa.String(length=64), nullable=True))
    add_column_if_missing('vehicles', sa.Column('facebook_posted_at', sa.DateTime(), nullable=True))
    add_column_if_missing('vehicles', sa.Column('facebook_last_error', sa.String(length=500), nullable=True))
    add_column_if_missing('vehicles', sa.Column('facebook_last_status', sa.String(length=32), nullable=True))


def downgrade():
    if not has_table('vehicles'):
        return
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        for col in (
            'facebook_last_status',
            'facebook_last_error',
            'facebook_posted_at',
            'facebook_post_id',
        ):
            if has_column('vehicles', col):
                batch_op.drop_column(col)
