"""Persist controlled retry provenance without reusing a completed manifest."""

import sqlalchemy as sa
from alembic import op


revision = '20260914_0010'
down_revision = '20260914_0009'
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    return {column['name'] for column in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    return {index['name'] for index in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if 'retry_of_cycle_id' not in _column_names(bind, 'monitoring_cycles'):
        op.add_column('monitoring_cycles', sa.Column('retry_of_cycle_id', sa.String(), nullable=True))
    if 'ix_monitoring_cycles_retry_of_cycle_id' not in _index_names(bind, 'monitoring_cycles'):
        op.create_index(
            'ix_monitoring_cycles_retry_of_cycle_id',
            'monitoring_cycles',
            ['retry_of_cycle_id'],
        )


def downgrade() -> None:
    raise RuntimeError('Cycle retry lineage is append-only; restore a verified backup instead.')
