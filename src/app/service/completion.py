from __future__ import annotations

from typing import Any


def _count(summary: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(summary.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def summarize_cycle_completion(
    search_summary: dict[str, Any], direct_cards_summary: dict[str, Any]
) -> dict[str, Any]:
    """Return the only status that may represent a completed monitoring cycle.

    Search visibility and direct-card inspection are separate mandatory facts.
    A successful search cannot turn an unfinished or blocked direct-card stage
    into a successful cycle.
    """
    search_errors = _count(search_summary, 'technical_errors')
    direct_errors = _count(direct_cards_summary, 'technical_errors')
    links_need_review = _count(search_summary, 'links_need_review')
    direct_incomplete = _count(direct_cards_summary, 'incomplete')

    reasons: list[str] = []
    if search_errors:
        reasons.append('search_technical_errors')
    if direct_errors:
        reasons.append('direct_card_technical_errors')
    if links_need_review:
        reasons.append('links_need_review')
    if direct_incomplete and not direct_errors:
        reasons.append('direct_cards_incomplete')

    return {
        'status': 'partial' if reasons else 'completed',
        'technical_errors': search_errors + direct_errors,
        'search_technical_errors': search_errors,
        'direct_cards_technical_errors': direct_errors,
        'links_need_review': links_need_review,
        'direct_cards_incomplete': direct_incomplete,
        'partial_reasons': reasons,
    }
