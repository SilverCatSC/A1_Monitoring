"""Bind operator feedback to explainable reconciliation findings."""

import sqlalchemy as sa
from alembic import op


revision = '20260914_0009'
down_revision = '20260914_0008'
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    return {column['name'] for column in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    return {index['name'] for index in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if 'reconciliation_id' not in _column_names(bind, 'manager_feedback'):
        # Keep this additive for SQLite and legacy deployments; the ORM relation
        # is present for new schemas and the row is still validated by the service.
        op.add_column('manager_feedback', sa.Column('reconciliation_id', sa.String(), nullable=True))
    if 'finding_code' not in _column_names(bind, 'manager_feedback'):
        op.add_column('manager_feedback', sa.Column('finding_code', sa.String(), nullable=True))
    if 'ix_manager_feedback_reconciliation_id' not in _index_names(bind, 'manager_feedback'):
        op.create_index(
            'ix_manager_feedback_reconciliation_id',
            'manager_feedback',
            ['reconciliation_id'],
        )


def downgrade() -> None:
    raise RuntimeError('Operator feedback links are historical records; restore a verified backup instead.')
