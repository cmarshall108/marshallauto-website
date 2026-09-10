"""add User login-lockout + last-login audit fields (admin security hardening)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'user', sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    add_column_if_missing('user', sa.Column('locked_until', sa.DateTime(), nullable=True))
    add_column_if_missing('user', sa.Column('last_login_at', sa.DateTime(), nullable=True))
    add_column_if_missing('user', sa.Column('last_login_ip', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('last_login_ip')
        batch_op.drop_column('last_login_at')
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_attempts')
