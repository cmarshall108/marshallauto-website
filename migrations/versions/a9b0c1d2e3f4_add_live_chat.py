"""Add persistent live chat conversations, messages and staff presence."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing, has_table

revision = 'a9b0c1d2e3f4'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'chat_presence',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('auth_version', sa.String(32), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    )
    create_table_if_missing(
        'chat_conversations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id', ondelete='SET NULL'), nullable=True),
        sa.Column('customer_name', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_id', sa.Integer(), nullable=False),
        sa.Column('last_customer_message_id', sa.Integer(), nullable=False),
        sa.Column('last_read_message_id', sa.Integer(), nullable=False),
    )
    create_table_if_missing(
        'chat_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('conversation_id', sa.String(36),
                  sa.ForeignKey('chat_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_nonce', sa.String(36), nullable=False),
        sa.Column('sender', sa.String(16), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('body', sa.String(5000), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('conversation_id', 'client_nonce', name='uq_chat_message_nonce'),
    )
    for table, columns in (
        ('chat_presence', ('user_id', 'last_seen_at')),
        ('chat_conversations', ('updated_at', 'closed_at')),
        ('chat_messages', ('conversation_id',)),
    ):
        for column in columns:
            create_index_if_missing(f'ix_{table}_{column}', table, [column])


def downgrade():
    for table in ('chat_messages', 'chat_conversations', 'chat_presence'):
        if has_table(table):
            op.drop_table(table)
