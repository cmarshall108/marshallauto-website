"""Add private scanned documents for sold-vehicle sale details."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing

revision = 'b1c2d3e4f5a7'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'vehicle_sale_documents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('filename', sa.String(128), nullable=False),
        sa.Column('original_filename', sa.String(255), nullable=True),
        sa.Column('content_type', sa.String(64), nullable=False),
        sa.Column('printer_label', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    create_index_if_missing(
        'ix_vehicle_sale_documents_vehicle_id', 'vehicle_sale_documents', ['vehicle_id'],
    )


def downgrade():
    op.drop_index('ix_vehicle_sale_documents_vehicle_id', table_name='vehicle_sale_documents')
    op.drop_table('vehicle_sale_documents')
