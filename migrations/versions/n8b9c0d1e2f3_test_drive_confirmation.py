"""Add test-drive confirmation and per-staff reminder dismissals."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing

revision = 'n8b9c0d1e2f3'
down_revision = 'm7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('test_drives', sa.Column('confirmation_sms_status', sa.String(20), nullable=True))
    add_column_if_missing('test_drives', sa.Column('scheduled_at', sa.DateTime(), nullable=True))
    add_column_if_missing('test_drives', sa.Column('confirmed_at', sa.DateTime(), nullable=True))
    op.execute("UPDATE test_drives SET scheduled_at = started_at WHERE status = 'requested' AND scheduled_at IS NULL")
    if not sa.inspect(op.get_bind()).has_table('test_drive_reminder_dismissals'):
        op.create_table(
            'test_drive_reminder_dismissals',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('test_drive_id', sa.Integer(), sa.ForeignKey('test_drives.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('phase', sa.String(20), nullable=False),
            sa.UniqueConstraint('test_drive_id', 'user_id', 'phase', name='uq_test_drive_reminder_user_phase'),
        )


def downgrade():
    op.drop_table('test_drive_reminder_dismissals')
    with op.batch_alter_table('test_drives') as batch_op:
        batch_op.drop_column('confirmed_at')
        batch_op.drop_column('scheduled_at')
        batch_op.drop_column('confirmation_sms_status')
