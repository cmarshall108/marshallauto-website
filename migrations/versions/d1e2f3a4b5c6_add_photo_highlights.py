"""add photo highlights and analysis job queue

Revision ID: d1e2f3a4b5c6
Revises: c4f9e2a1b8d0
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
    has_column,
    has_index,
    has_table,
)


# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c4f9e2a1b8d0'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        'vehicle_images',
        sa.Column('highlight_status', sa.String(length=20), nullable=False, server_default='pending'),
    )
    add_column_if_missing(
        'vehicle_images',
        sa.Column('highlight_error', sa.String(length=500), nullable=True),
    )
    add_column_if_missing(
        'vehicle_images',
        sa.Column('highlight_scene', sa.String(length=64), nullable=True),
    )
    add_column_if_missing(
        'vehicle_images',
        sa.Column('highlight_analyzed_at', sa.DateTime(), nullable=True),
    )
    add_column_if_missing(
        'vehicle_images',
        sa.Column('highlight_version', sa.Integer(), nullable=True),
    )
    create_index_if_missing(
        'ix_vehicle_images_highlight_status',
        'vehicle_images',
        ['highlight_status'],
    )

    create_table_if_missing(
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
    create_index_if_missing(
        'ix_vehicle_image_highlights_vehicle_image_id',
        'vehicle_image_highlights',
        ['vehicle_image_id'],
    )

    create_table_if_missing(
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
    create_index_if_missing('ix_photo_highlight_jobs_vehicle_image_id', 'photo_highlight_jobs', ['vehicle_image_id'])
    create_index_if_missing('ix_photo_highlight_jobs_vehicle_id', 'photo_highlight_jobs', ['vehicle_id'])
    create_index_if_missing('ix_photo_highlight_jobs_status', 'photo_highlight_jobs', ['status'])
    create_index_if_missing('ix_photo_highlight_jobs_priority', 'photo_highlight_jobs', ['priority'])
    create_index_if_missing('ix_photo_highlight_jobs_lease_expires_at', 'photo_highlight_jobs', ['lease_expires_at'])
    create_index_if_missing('ix_photo_highlight_jobs_scheduled_at', 'photo_highlight_jobs', ['scheduled_at'])


def downgrade():
    if has_table('photo_highlight_jobs'):
        for name in (
            'ix_photo_highlight_jobs_scheduled_at',
            'ix_photo_highlight_jobs_lease_expires_at',
            'ix_photo_highlight_jobs_priority',
            'ix_photo_highlight_jobs_status',
            'ix_photo_highlight_jobs_vehicle_id',
            'ix_photo_highlight_jobs_vehicle_image_id',
        ):
            if has_index('photo_highlight_jobs', name):
                op.drop_index(name, table_name='photo_highlight_jobs')
        op.drop_table('photo_highlight_jobs')

    if has_table('vehicle_image_highlights'):
        if has_index('vehicle_image_highlights', 'ix_vehicle_image_highlights_vehicle_image_id'):
            op.drop_index('ix_vehicle_image_highlights_vehicle_image_id', table_name='vehicle_image_highlights')
        op.drop_table('vehicle_image_highlights')

    if has_table('vehicle_images'):
        with op.batch_alter_table('vehicle_images', schema=None) as batch_op:
            if has_index('vehicle_images', 'ix_vehicle_images_highlight_status'):
                batch_op.drop_index('ix_vehicle_images_highlight_status')
            for col in (
                'highlight_version',
                'highlight_analyzed_at',
                'highlight_scene',
                'highlight_error',
                'highlight_status',
            ):
                if has_column('vehicle_images', col):
                    batch_op.drop_column(col)
