"""Add live-chat spam review and staff blocking."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, has_column

revision = 'b1c2d3e4f5a6'
down_revision = 'a9b0c1d2e3f4'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('chat_conversations', sa.Column('blocked_at', sa.DateTime(), nullable=True))
    add_column_if_missing('chat_conversations', sa.Column(
        'is_suspicious', sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    add_column_if_missing('chat_messages', sa.Column(
        'is_suspicious', sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    add_column_if_missing('chat_messages', sa.Column(
        'spam_score', sa.Integer(), nullable=False, server_default='0',
    ))
    add_column_if_missing('chat_messages', sa.Column('spam_reasons', sa.String(512), nullable=True))


def downgrade():
    for table, columns in (
        ('chat_messages', ('spam_reasons', 'spam_score', 'is_suspicious')),
        ('chat_conversations', ('is_suspicious', 'blocked_at')),
    ):
        for column in columns:
            if has_column(table, column):
                with op.batch_alter_table(table) as batch:
                    batch.drop_column(column)
