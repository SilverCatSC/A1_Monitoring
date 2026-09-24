"""Compatibility import for the read-only Auto.ru placement comparison."""

from app.service.marketplace_placement_reconciliation import (
    OpenedAutoRuCard,
    PlacementFinding,
    reconcile_autoru_placements,
)

__all__ = ['OpenedAutoRuCard', 'PlacementFinding', 'reconcile_autoru_placements']
