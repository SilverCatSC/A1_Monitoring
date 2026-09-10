"""Keep confirmed links and seller reconciliation history without rewriting observations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260909_0006'
down_revision = '20260908_0005'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    def source_type():
        if bind.dialect.name == 'postgresql':
            return postgresql.ENUM('AUTO_RU', 'AVITO', name='enginetype', create_type=False)
        return sa.Enum('AUTO_RU', 'AVITO', name='enginetype')
    tables = sa.inspect(bind).get_table_names()
    if 'listing_link_overrides' not in tables:
        op.create_table('listing_link_overrides',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('listing_id', sa.String(), sa.ForeignKey('listings.id'), nullable=False),
            sa.Column('source', source_type(), nullable=False),
            sa.Column('url', sa.Text(), nullable=False),
            sa.Column('last_source_url', sa.Text()),
            sa.Column('actor', sa.String(), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('listing_id', 'source', name='uq_listing_link_override'))
        op.create_index('ix_listing_link_overrides_listing_id', 'listing_link_overrides', ['listing_id'])
    if 'listing_reconciliations' not in tables:
        op.create_table('listing_reconciliations',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('batch_id', sa.String(), nullable=False),
            sa.Column('listing_id', sa.String(), sa.ForeignKey('listings.id'), nullable=False),
            sa.Column('source', source_type(), nullable=False),
            sa.Column('state', sa.String(), nullable=False),
            sa.Column('url', sa.Text()),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('candidates', sa.JSON(), nullable=False),
            sa.Column('details', sa.JSON(), nullable=False),
            sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        for column in ('batch_id', 'listing_id', 'state'):
            op.create_index(f'ix_listing_reconciliations_{column}', 'listing_reconciliations', [column])


def downgrade():
    op.drop_table('listing_reconciliations')
    op.drop_table('listing_link_overrides')
