"""Record the network provenance of every live marketplace observation.

Revision ID: 20260904_0003
Revises: 20260904_0002
"""

import sqlalchemy as sa
from alembic import op

revision = '20260904_0003'
down_revision = '20260904_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        'scan_runs',
        'dealer_discovery_runs',
        'dealer_listing_candidates',
    ):
        columns = {column['name'] for column in sa.inspect(bind).get_columns(table_name)}
        if 'network_profile' not in columns:
            op.add_column(
                table_name,
                sa.Column(
                    'network_profile',
                    sa.String(),
                    server_default='unknown',
                    nullable=False,
                ),
            )


def downgrade() -> None:
    op.drop_column('dealer_listing_candidates', 'network_profile')
    op.drop_column('dealer_discovery_runs', 'network_profile')
    op.drop_column('scan_runs', 'network_profile')
