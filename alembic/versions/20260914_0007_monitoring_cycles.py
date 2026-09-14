"""Add a durable cycle ledger and correlations for each monitoring stage."""

from alembic import op
import sqlalchemy as sa


revision = '20260914_0007'
down_revision = '20260909_0006'
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    return {column['name'] for column in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    return {index['name'] for index in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'monitoring_cycles' not in tables:
        op.create_table(
            'monitoring_cycles',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('network_profile', sa.String(), nullable=False),
            sa.Column('app_version', sa.String(), nullable=False),
            sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('finished_at', sa.DateTime(timezone=True)),
            sa.Column('roster_count', sa.Integer()),
            sa.Column('roster_sha256', sa.String()),
            sa.Column('manifest_path', sa.Text()),
            sa.Column('summary', sa.JSON()),
            sa.Column('error', sa.Text()),
        )
    if 'ix_monitoring_cycles_status' not in _index_names(bind, 'monitoring_cycles'):
        op.create_index('ix_monitoring_cycles_status', 'monitoring_cycles', ['status'])

    for table_name in (
        'source_import_snapshots',
        'dealer_discovery_runs',
        'listing_reconciliations',
        'scan_runs',
    ):
        if 'cycle_id' not in _column_names(bind, table_name):
            op.add_column(table_name, sa.Column('cycle_id', sa.String(), nullable=True))
        index_name = f'ix_{table_name}_cycle_id'
        if index_name not in _index_names(bind, table_name):
            op.create_index(index_name, table_name, ['cycle_id'])


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        'source_import_snapshots',
        'dealer_discovery_runs',
        'listing_reconciliations',
        'scan_runs',
    ):
        index_name = f'ix_{table_name}_cycle_id'
        if index_name in _index_names(bind, table_name):
            op.drop_index(index_name, table_name=table_name)
    op.drop_index('ix_monitoring_cycles_status', table_name='monitoring_cycles')
    op.drop_table('monitoring_cycles')
