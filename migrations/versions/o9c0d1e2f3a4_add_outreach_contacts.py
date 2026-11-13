"""Add outreach_contacts for monthly new-car texts and emails."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import create_index_if_missing, create_table_if_missing

revision = 'o9c0d1e2f3a4'
down_revision = 'n8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade():
    create_table_if_missing(
        'outreach_contacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('email', sa.String(length=128), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('phone_e164', sa.String(length=20), nullable=True),
        sa.Column('last_lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('source', sa.String(length=64), nullable=True),
        sa.Column('sms_opt_in', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('email_opt_in', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('sms_opted_out_at', sa.DateTime(), nullable=True),
        sa.Column('email_opted_out_at', sa.DateTime(), nullable=True),
        sa.Column('opt_out_reason', sa.String(length=128), nullable=True),
        sa.Column('unsubscribe_token', sa.String(length=64), nullable=False),
        sa.Column('last_sms_at', sa.DateTime(), nullable=True),
        sa.Column('last_email_at', sa.DateTime(), nullable=True),
        sa.Column('last_outreach_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('phone_e164', name='uq_outreach_contacts_phone_e164'),
        sa.UniqueConstraint('unsubscribe_token', name='uq_outreach_contacts_unsubscribe_token'),
    )
    create_index_if_missing('ix_outreach_contacts_email', 'outreach_contacts', ['email'])
    create_index_if_missing('ix_outreach_contacts_phone_e164', 'outreach_contacts', ['phone_e164'])
    create_index_if_missing('ix_outreach_contacts_last_lead_id', 'outreach_contacts', ['last_lead_id'])
    create_index_if_missing('ix_outreach_contacts_sms_opt_in', 'outreach_contacts', ['sms_opt_in'])
    create_index_if_missing('ix_outreach_contacts_email_opt_in', 'outreach_contacts', ['email_opt_in'])
    create_index_if_missing('ix_outreach_contacts_unsubscribe_token', 'outreach_contacts', ['unsubscribe_token'])
    create_index_if_missing('ix_outreach_contacts_last_outreach_at', 'outreach_contacts', ['last_outreach_at'])


def downgrade():
    op.drop_table('outreach_contacts')
