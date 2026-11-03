"""Store encrypted financing application PDFs.

Revision ID: h2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-11-02

"""
import sqlalchemy as sa
from alembic import op

from migrations.compat import create_index_if_missing, create_table_if_missing

revision = 'h2b3c4d5e6f7'
down_revision = 'g1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'financing_applications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'vehicle_id', sa.Integer(),
            sa.ForeignKey('vehicles.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('encrypted_pdf_filename', sa.String(length=64), nullable=False),
        sa.Column('joint_credit', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('encrypted_pdf_filename'),
    )
    create_index_if_missing(
        'ix_financing_applications_vehicle_id',
        'financing_applications', ['vehicle_id'],
    )
    create_index_if_missing(
        'ix_financing_applications_submitted_at',
        'financing_applications', ['submitted_at'],
    )


def downgrade():
    op.drop_index(
        'ix_financing_applications_submitted_at',
        table_name='financing_applications',
    )
    op.drop_index(
        'ix_financing_applications_vehicle_id',
        table_name='financing_applications',
    )
    op.drop_table('financing_applications')
