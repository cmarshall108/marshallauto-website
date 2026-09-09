"""add vehicle video_url and blog_posts table

Revision ID: b7c8d9e0f1a2
Revises: f4b7c9d2e1a3
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing, create_table_if_missing, has_table


# revision identifiers, used by Alembic.
revision = 'b7c8d9e0f1a2'
down_revision = 'f4b7c9d2e1a3'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('video_url', sa.String(length=500), nullable=True))

    create_table_if_missing(
        'blog_posts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=220), nullable=True),
        sa.Column('excerpt', sa.String(length=320), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('cover_image_url', sa.String(length=500), nullable=True),
        sa.Column('author_name', sa.String(length=128), nullable=True),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('seo_title', sa.String(length=160), nullable=True),
        sa.Column('seo_description', sa.String(length=320), nullable=True),
        sa.UniqueConstraint('slug', name='uq_blog_posts_slug'),
    )
    if has_table('blog_posts'):
        create_index_if_missing('ix_blog_posts_slug', 'blog_posts', ['slug'])
        create_index_if_missing('ix_blog_posts_is_published', 'blog_posts', ['is_published'])


def downgrade():
    op.drop_table('blog_posts')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('video_url')
