"""Separate operator link review from a marketplace technical failure."""

from alembic import op


revision = '20260914_0011'
down_revision = '20260914_0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE observationstate ADD VALUE IF NOT EXISTS 'review_required'")


def downgrade() -> None:
    raise RuntimeError('Observation-state history is append-only; restore a verified backup instead.')
