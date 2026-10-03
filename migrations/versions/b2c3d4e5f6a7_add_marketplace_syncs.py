"""Add persistent Marketplace import and refresh tracking."""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    if 'marketplace_syncs' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            'marketplace_syncs',
            sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), primary_key=True),
            sa.Column('source_url', sa.String(256), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False),
            sa.Column('snapshot', sa.JSON(), nullable=False),
            sa.Column('photo_files', sa.JSON(), nullable=False),
            sa.Column('next_check_at', sa.DateTime(), nullable=False),
            sa.Column('last_checked_at', sa.DateTime(), nullable=True),
            sa.Column('last_success_at', sa.DateTime(), nullable=True),
            sa.Column('last_error', sa.String(500), nullable=True),
        )
        op.create_index('ix_marketplace_syncs_next_check_at', 'marketplace_syncs', ['next_check_at'])


def downgrade():
    op.drop_table('marketplace_syncs')