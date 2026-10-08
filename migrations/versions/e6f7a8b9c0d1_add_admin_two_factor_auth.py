"""Add encrypted admin TOTP enrollment and single-use recovery codes."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing, create_table_if_missing

revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('user', sa.Column('totp_secret_encrypted', sa.Text(), nullable=True))
    add_column_if_missing('user', sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    add_column_if_missing('user', sa.Column('totp_last_counter', sa.BigInteger(), nullable=True))
    add_column_if_missing('user', sa.Column('auth_version', sa.String(32), nullable=True))
    create_table_if_missing(
        'admin_recovery_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('code_hash', sa.String(64), nullable=False, unique=True),
    )
    create_index_if_missing('ix_admin_recovery_codes_user_id', 'admin_recovery_codes', ['user_id'])


def downgrade():
    op.drop_table('admin_recovery_codes')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('auth_version')
        batch_op.drop_column('totp_last_counter')
        batch_op.drop_column('totp_enabled')
        batch_op.drop_column('totp_secret_encrypted')
