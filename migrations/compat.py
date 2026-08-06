"""Idempotent helpers for Alembic upgrades on partially bootstrapped DBs."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def inspector():
    return sa.inspect(op.get_bind())


def has_table(table_name: str) -> bool:
    return table_name in set(inspector().get_table_names())


def has_column(table_name: str, column_name: str) -> bool:
    if not has_table(table_name):
        return False
    return column_name in {c['name'] for c in inspector().get_columns(table_name)}


def has_index(table_name: str, index_name: str) -> bool:
    if not has_table(table_name):
        return False
    return index_name in {ix['name'] for ix in inspector().get_indexes(table_name)}


def add_column_if_missing(table_name: str, column: sa.Column) -> None:
    """Add a column only when the table exists and the column does not."""
    if not has_table(table_name) or has_column(table_name, column.name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.add_column(column)


def create_index_if_missing(index_name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if not has_table(table_name) or has_index(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def create_table_if_missing(table_name: str, *columns_and_constraints) -> None:
    if has_table(table_name):
        return
    op.create_table(table_name, *columns_and_constraints)
