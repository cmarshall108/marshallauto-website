"""Add durable background video compression jobs."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'video_upload_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=True),
        sa.Column('input_filename', sa.String(128), nullable=False),
        sa.Column('previous_url', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('claim_token', sa.String(32), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    create_index_if_missing('ix_video_upload_jobs_vehicle_id', 'video_upload_jobs', ['vehicle_id'])
    create_index_if_missing('ix_video_upload_jobs_status', 'video_upload_jobs', ['status'])


def downgrade():
    op.drop_table('video_upload_jobs')
