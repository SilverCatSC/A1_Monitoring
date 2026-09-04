"""Add audited dealer listing discovery catalog.

Revision ID: 20260904_0002
Revises: 20260904_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '20260904_0002'
down_revision = '20260904_0001'
branch_labels = None
depends_on = None


def _source_type(bind):
    if bind.dialect.name == 'postgresql':
        return postgresql.ENUM('AUTO_RU', 'AVITO', name='enginetype', create_type=False)
    return sa.Enum('AUTO_RU', 'AVITO', name='enginetype')


def upgrade() -> None:
    bind = op.get_bind()
    source_type = _source_type(bind)
    op.create_table(
        'dealer_discovery_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('source', source_type, nullable=False),
        sa.Column('dealer_url', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('complete', sa.Boolean(), nullable=False),
        sa.Column('pages_scanned', sa.Integer(), nullable=False),
        sa.Column('candidates_found', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('diagnostics', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dealer_discovery_runs_source', 'dealer_discovery_runs', ['source'])
    op.create_table(
        'dealer_listing_candidates',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('source', _source_type(bind), nullable=False),
        sa.Column('external_key', sa.String(), nullable=False),
        sa.Column('dealer_url', sa.Text(), nullable=False),
        sa.Column('listing_url', sa.Text(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('price_hint', sa.Float(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('raw_payload', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'external_key', name='uq_dealer_candidate_source_key'),
    )
    op.create_index('ix_dealer_listing_candidates_source', 'dealer_listing_candidates', ['source'])
    op.create_index('ix_dealer_listing_candidates_active', 'dealer_listing_candidates', ['active'])


def downgrade() -> None:
    op.drop_index('ix_dealer_listing_candidates_active', table_name='dealer_listing_candidates')
    op.drop_index('ix_dealer_listing_candidates_source', table_name='dealer_listing_candidates')
    op.drop_table('dealer_listing_candidates')
    op.drop_index('ix_dealer_discovery_runs_source', table_name='dealer_discovery_runs')
    op.drop_table('dealer_discovery_runs')
