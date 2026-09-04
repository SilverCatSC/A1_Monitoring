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
    Base.metadata.create_all(bind=bind, checkfirst=True)
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
