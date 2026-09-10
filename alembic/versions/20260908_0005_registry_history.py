"""Record registry price, availability and URL changes without rewriting observations."""
import sqlalchemy as sa
from alembic import op

revision = '20260908_0005'
down_revision = '20260904_0004'
branch_labels = None
depends_on = None


def upgrade():
    if 'listing_change_events' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            'listing_change_events',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('listing_id', sa.String(), sa.ForeignKey('listings.id'), nullable=False),
            sa.Column('snapshot_id', sa.String(), sa.ForeignKey('source_import_snapshots.id')),
            sa.Column('actor', sa.String(), nullable=False),
            sa.Column('kind', sa.String(), nullable=False),
            sa.Column('changes', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_listing_change_events_listing_id', 'listing_change_events', ['listing_id'])


def downgrade():
    op.drop_table('listing_change_events')
