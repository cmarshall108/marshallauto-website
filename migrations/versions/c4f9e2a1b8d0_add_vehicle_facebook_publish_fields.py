"""add vehicle facebook publish fields

Revision ID: c4f9e2a1b8d0
Revises: a8be384dab1e
Create Date: 2026-03-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4f9e2a1b8d0'
down_revision = 'a8be384dab1e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('facebook_post_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('facebook_posted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('facebook_last_error', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('facebook_last_status', sa.String(length=32), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('facebook_last_status')
        batch_op.drop_column('facebook_last_error')
        batch_op.drop_column('facebook_posted_at')
        batch_op.drop_column('facebook_post_id')
