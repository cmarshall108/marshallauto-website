"""add first-party page views and analytics events

Revision ID: e3a4b5c6d7e8
Revises: d1e2f3a4b5c6
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3a4b5c6d7e8'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'page_views',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('visitor_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('path', sa.String(length=512), nullable=False),
        sa.Column('page_type', sa.String(length=64), nullable=True),
        sa.Column('page_title', sa.String(length=255), nullable=True),
        sa.Column('vehicle_id', sa.Integer(), nullable=True),
        sa.Column('referrer', sa.String(length=512), nullable=True),
        sa.Column('referrer_host', sa.String(length=255), nullable=True),
        sa.Column('landing_path', sa.String(length=512), nullable=True),
        sa.Column('query_string', sa.String(length=512), nullable=True),
        sa.Column('utm_source', sa.String(length=128), nullable=True),
        sa.Column('utm_medium', sa.String(length=128), nullable=True),
        sa.Column('utm_campaign', sa.String(length=128), nullable=True),
        sa.Column('utm_term', sa.String(length=128), nullable=True),
        sa.Column('utm_content', sa.String(length=128), nullable=True),
        sa.Column('gclid', sa.String(length=255), nullable=True),
        sa.Column('fbclid', sa.String(length=255), nullable=True),
        sa.Column('device_type', sa.String(length=32), nullable=True),
        sa.Column('browser', sa.String(length=64), nullable=True),
        sa.Column('os', sa.String(length=64), nullable=True),
        sa.Column('language', sa.String(length=32), nullable=True),
        sa.Column('screen_width', sa.Integer(), nullable=True),
        sa.Column('screen_height', sa.Integer(), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=True),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=False),
        sa.Column('scroll_depth_pct', sa.Integer(), nullable=False),
        sa.Column('is_bounce', sa.Boolean(), nullable=False),
        sa.Column('is_engaged', sa.Boolean(), nullable=False),
        sa.Column('is_exit', sa.Boolean(), nullable=False),
        sa.Column('heartbeat_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_page_views_created_at', 'page_views', ['created_at'], unique=False)
    op.create_index('ix_page_views_visitor_id', 'page_views', ['visitor_id'], unique=False)
    op.create_index('ix_page_views_session_id', 'page_views', ['session_id'], unique=False)
    op.create_index('ix_page_views_path', 'page_views', ['path'], unique=False)
    op.create_index('ix_page_views_page_type', 'page_views', ['page_type'], unique=False)
    op.create_index('ix_page_views_vehicle_id', 'page_views', ['vehicle_id'], unique=False)
    op.create_index('ix_page_views_referrer_host', 'page_views', ['referrer_host'], unique=False)
    op.create_index('ix_page_views_utm_source', 'page_views', ['utm_source'], unique=False)
    op.create_index('ix_page_views_utm_campaign', 'page_views', ['utm_campaign'], unique=False)
    op.create_index('ix_page_views_device_type', 'page_views', ['device_type'], unique=False)
    op.create_index('ix_page_views_ip_hash', 'page_views', ['ip_hash'], unique=False)

    op.create_table(
        'analytics_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('visitor_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('page_view_id', sa.Integer(), nullable=True),
        sa.Column('event_name', sa.String(length=64), nullable=False),
        sa.Column('event_category', sa.String(length=64), nullable=True),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('path', sa.String(length=512), nullable=True),
        sa.Column('page_type', sa.String(length=64), nullable=True),
        sa.Column('vehicle_id', sa.Integer(), nullable=True),
        sa.Column('meta_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['page_view_id'], ['page_views.id'], ),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_analytics_events_created_at', 'analytics_events', ['created_at'], unique=False)
    op.create_index('ix_analytics_events_visitor_id', 'analytics_events', ['visitor_id'], unique=False)
    op.create_index('ix_analytics_events_session_id', 'analytics_events', ['session_id'], unique=False)
    op.create_index('ix_analytics_events_page_view_id', 'analytics_events', ['page_view_id'], unique=False)
    op.create_index('ix_analytics_events_event_name', 'analytics_events', ['event_name'], unique=False)
    op.create_index('ix_analytics_events_event_category', 'analytics_events', ['event_category'], unique=False)
    op.create_index('ix_analytics_events_page_type', 'analytics_events', ['page_type'], unique=False)
    op.create_index('ix_analytics_events_vehicle_id', 'analytics_events', ['vehicle_id'], unique=False)


def downgrade():
    op.drop_table('analytics_events')
    op.drop_table('page_views')
