"""add spam triage fields to leads

Revision ID: f4b7c9d2e1a3
Revises: e3a4b5c6d7e8
Create Date: 2026-09-08

"""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing, has_column


# revision identifiers, used by Alembic.
revision = 'f4b7c9d2e1a3'
down_revision = 'e3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('leads', sa.Column('is_spam', sa.Boolean(), nullable=False, server_default=sa.false()))
    add_column_if_missing('leads', sa.Column('spam_score', sa.Integer(), nullable=False, server_default='0'))
    add_column_if_missing('leads', sa.Column('spam_reasons', sa.String(length=512), nullable=True))
    create_index_if_missing('ix_leads_is_spam', 'leads', ['is_spam'])


def downgrade():
    op.drop_index('ix_leads_is_spam', table_name='leads')
    with op.batch_alter_table('leads', schema=None) as batch_op:
        for column in ('spam_reasons', 'spam_score', 'is_spam'):
            if has_column('leads', column):
                batch_op.drop_column(column)
