"""add photo highlights and analysis job queue

Revision ID: d1e2f3a4b5c6
Revises: c4f9e2a1b8d0
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c4f9e2a1b8d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicle_images', schema=None) as batch_op:
        batch_op.add_column(sa.Column('highlight_status', sa.String(length=20), nullable=False, server_default='pending'))
        batch_op.add_column(sa.Column('highlight_error', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('highlight_scene', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('highlight_analyzed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('highlight_version', sa.Integer(), nullable=True))
        batch_op.create_index('ix_vehicle_images_highlight_status', ['highlight_status'], unique=False)

    op.create_table(
        'vehicle_image_highlights',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vehicle_image_id', sa.Integer(), nullable=False),
        sa.Column('x_pct', sa.Float(), nullable=False),
        sa.Column('y_pct', sa.Float(), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon', sa.String(length=64), nullable=True),
        sa.Column('severity', sa.String(length=32), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False),
        sa.Column('is_visible', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['vehicle_image_id'], ['vehicle_images.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_vehicle_image_highlights_vehicle_image_id', 'vehicle_image_highlights', ['vehicle_image_id'], unique=False)

    op.create_table(
        'photo_highlight_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vehicle_image_id', sa.Integer(), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('locked_by', sa.String(length=128), nullable=True),
        sa.Column('locked_at', sa.DateTime(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('result_summary', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ),
        sa.ForeignKeyConstraint(['vehicle_image_id'], ['vehicle_images.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_photo_highlight_jobs_vehicle_image_id', 'photo_highlight_jobs', ['vehicle_image_id'], unique=False)
    op.create_index('ix_photo_highlight_jobs_vehicle_id', 'photo_highlight_jobs', ['vehicle_id'], unique=False)
    op.create_index('ix_photo_highlight_jobs_status', 'photo_highlight_jobs', ['status'], unique=False)
    op.create_index('ix_photo_highlight_jobs_priority', 'photo_highlight_jobs', ['priority'], unique=False)
    op.create_index('ix_photo_highlight_jobs_lease_expires_at', 'photo_highlight_jobs', ['lease_expires_at'], unique=False)
    op.create_index('ix_photo_highlight_jobs_scheduled_at', 'photo_highlight_jobs', ['scheduled_at'], unique=False)


def downgrade():
    op.drop_index('ix_photo_highlight_jobs_scheduled_at', table_name='photo_highlight_jobs')
    op.drop_index('ix_photo_highlight_jobs_lease_expires_at', table_name='photo_highlight_jobs')
    op.drop_index('ix_photo_highlight_jobs_priority', table_name='photo_highlight_jobs')
    op.drop_index('ix_photo_highlight_jobs_status', table_name='photo_highlight_jobs')
    op.drop_index('ix_photo_highlight_jobs_vehicle_id', table_name='photo_highlight_jobs')
    op.drop_index('ix_photo_highlight_jobs_vehicle_image_id', table_name='photo_highlight_jobs')
    op.drop_table('photo_highlight_jobs')

    op.drop_index('ix_vehicle_image_highlights_vehicle_image_id', table_name='vehicle_image_highlights')
    op.drop_table('vehicle_image_highlights')

    with op.batch_alter_table('vehicle_images', schema=None) as batch_op:
        batch_op.drop_index('ix_vehicle_images_highlight_status')
        batch_op.drop_column('highlight_version')
        batch_op.drop_column('highlight_analyzed_at')
        batch_op.drop_column('highlight_scene')
        batch_op.drop_column('highlight_error')
        batch_op.drop_column('highlight_status')
