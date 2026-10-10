"""Add a public coming-soon flag to vehicles."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing


revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'vehicles',
        sa.Column('is_coming_soon', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    create_index_if_missing('ix_vehicles_is_coming_soon', 'vehicles', ['is_coming_soon'])


def downgrade():
    op.drop_index('ix_vehicles_is_coming_soon', table_name='vehicles')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('is_coming_soon')
