"""Add NHTSA 5-star safety rating columns on vehicles."""
from alembic import op
import sqlalchemy as sa

from migrations.compat import add_column_if_missing, create_index_if_missing

revision = 'p0d1e2f3a4b5'
down_revision = 'o9c0d1e2f3a4'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('vehicles', sa.Column('nhtsa_overall_rating', sa.String(length=16), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_front_rating', sa.String(length=16), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_side_rating', sa.String(length=16), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_rollover_rating', sa.String(length=16), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_vehicle_id', sa.Integer(), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_safety_description', sa.String(length=160), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_safety_data', sa.Text(), nullable=True))
    add_column_if_missing('vehicles', sa.Column('nhtsa_safety_checked_at', sa.DateTime(), nullable=True))
    create_index_if_missing('ix_vehicles_nhtsa_overall_rating', 'vehicles', ['nhtsa_overall_rating'])
    create_index_if_missing('ix_vehicles_nhtsa_vehicle_id', 'vehicles', ['nhtsa_vehicle_id'])


def downgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_index('ix_vehicles_nhtsa_vehicle_id')
        batch_op.drop_index('ix_vehicles_nhtsa_overall_rating')
        batch_op.drop_column('nhtsa_safety_checked_at')
        batch_op.drop_column('nhtsa_safety_data')
        batch_op.drop_column('nhtsa_safety_description')
        batch_op.drop_column('nhtsa_vehicle_id')
        batch_op.drop_column('nhtsa_rollover_rating')
        batch_op.drop_column('nhtsa_side_rating')
        batch_op.drop_column('nhtsa_front_rating')
        batch_op.drop_column('nhtsa_overall_rating')
