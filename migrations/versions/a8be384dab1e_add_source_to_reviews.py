"""add source to reviews

Revision ID: a8be384dab1e
Revises: 7bec5a481d54
Create Date: 2026-07-17 16:29:12.650389

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing


# revision identifiers, used by Alembic.
revision = 'a8be384dab1e'
down_revision = '7bec5a481d54'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'reviews',
        sa.Column('source', sa.String(length=64), nullable=True),
    )


def downgrade():
    from migrations.compat import has_column, has_table

    if has_table('reviews') and has_column('reviews', 'source'):
        with op.batch_alter_table('reviews', schema=None) as batch_op:
            batch_op.drop_column('source')
