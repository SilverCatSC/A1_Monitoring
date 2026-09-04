"""Add absolute position for observation drill-down.

Revision ID: 20260904_0004
Revises: 20260904_0003
"""

import sqlalchemy as sa
from alembic import op

revision = '20260904_0004'
down_revision = '20260904_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column['name'] for column in sa.inspect(bind).get_columns('listing_observations')
    }
    if 'absolute_position' not in columns:
        op.add_column(
            'listing_observations',
            sa.Column('absolute_position', sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('listing_observations', 'absolute_position')
