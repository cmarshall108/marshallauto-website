"""Add the MVR-181 reconstructed override to vehicles."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing

revision = 'l6f7a8b9c0d1'
down_revision = 'k5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'vehicles',
        sa.Column('reconstructed', sa.String(8), nullable=False, server_default='auto'),
    )


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('reconstructed')
