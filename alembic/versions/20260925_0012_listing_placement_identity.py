"""Persist confirmed marketplace unique_id to registry listing bindings."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260925_0012'
down_revision = '20260914_0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    source_type = (
        postgresql.ENUM('AUTO_RU', 'AVITO', name='enginetype', create_type=False)
        if bind.dialect.name == 'postgresql'
        else sa.Enum('AUTO_RU', 'AVITO', name='enginetype')
    )
    op.create_table(
        'listing_placement_identities',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('listing_id', sa.String(), sa.ForeignKey('listings.id'), nullable=False),
        sa.Column('source', source_type, nullable=False),
        sa.Column('placement_id', sa.String(length=22), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('source', 'placement_id', name='uq_placement_identity_source_id'),
        sa.UniqueConstraint('source', 'listing_id', name='uq_placement_identity_source_listing'),
    )
    op.create_index('ix_listing_placement_identities_listing_id', 'listing_placement_identities', ['listing_id'])


def downgrade() -> None:
    op.drop_index('ix_listing_placement_identities_listing_id', table_name='listing_placement_identities')
    op.drop_table('listing_placement_identities')
