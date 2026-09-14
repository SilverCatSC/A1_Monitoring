"""Establish the versioned baseline for the current product schema.

Revision ID: 20260904_0001
Revises:
"""

from alembic import op

from app.models import Base

revision = '20260904_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # checkfirst makes the baseline safe for both a clean database and installations
    # created by the pre-Alembic application startup path.
    baseline_tables = [
        # Current models contain foreign keys to these append-only identity and
        # cycle parents.  Include them in a clean install so PostgreSQL never has
        # to create a child table before its referenced table exists.  Later
        # migrations remain responsible for their data backfills and indexes.
        'monitoring_cycles',
        'vehicles',
        'offers',
        'source_import_snapshots',
        'import_field_drift',
        'search_filters',
        'listings',
        'listing_link_events',
        'listing_link_overrides',
        'listing_reconciliations',
        'vehicle_filter_expectations',
        'scan_runs',
        'listing_observations',
        'absence_episodes',
        'manager_feedback',
        'feedback_events',
        'source_records',
        'offer_vehicle_links',
    ]
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in baseline_tables],
        checkfirst=True,
    )
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE absence_episodes DROP CONSTRAINT IF EXISTS uq_open_absence')
        op.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_open_absence_active '
            'ON absence_episodes (listing_id, filter_id, source) WHERE open IS TRUE'
        )
        op.execute(
            "ALTER TABLE manager_feedback ADD COLUMN IF NOT EXISTS "
            "category VARCHAR NOT NULL DEFAULT 'other'"
        )
        op.execute('ALTER TABLE manager_feedback ADD COLUMN IF NOT EXISTS assignee VARCHAR')


def downgrade() -> None:
    raise RuntimeError('Destructive baseline downgrade is unsupported; restore a verified backup')
