"""add reviews and vehicle meta keywords

Revision ID: 7bec5a481d54
Revises:
Create Date: 2026-07-17 16:23:18.257751

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing


# revision identifiers, used by Alembic.
revision = '7bec5a481d54'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Safe if column already exists from create_all / bootstrap ALTER.
    add_column_if_missing(
        'vehicles',
        sa.Column('meta_keywords', sa.String(length=255), nullable=True),
    )


def downgrade():
    from migrations.compat import has_column, has_table

    if has_table('vehicles') and has_column('vehicles', 'meta_keywords'):
        with op.batch_alter_table('vehicles', schema=None) as batch_op:
            batch_op.drop_column('meta_keywords')
