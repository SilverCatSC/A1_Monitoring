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
    # The direct-card stage's `incomplete` counts eligible cards only. Cards
    # excluded because their source/catalogue/identity is unresolved are still
    # unfinished work, even if no card navigation could be attempted.
    direct_unchecked = sum(_count(direct_cards_summary, key) for key in (
        'skipped_source_unavailable', 'skipped_catalogue_incomplete', 'skipped_unresolved',
    ))
    direct_incomplete = _count(direct_cards_summary, 'incomplete') + direct_unchecked
    catalogue_failures = search_summary.get('dealer_catalogue_failures') or []
    catalogue_errors = sum(bool(failure.get('technical_error', failure.get('error'))) for failure in catalogue_failures)

    reasons: list[str] = []
    if catalogue_errors:
        reasons.append('dealer_catalogue_unavailable')
    elif catalogue_failures:
        reasons.append('dealer_catalogue_incomplete')
    if search_errors:
        reasons.append('search_technical_errors')
    if direct_errors:
        reasons.append('direct_card_technical_errors')
    if links_need_review:
        reasons.append('links_need_review')
    if direct_incomplete and not direct_errors:
        reasons.append('direct_cards_incomplete')

    result = {
        'status': 'partial' if reasons else 'completed',
        'technical_errors': search_errors + direct_errors + catalogue_errors,
        'search_technical_errors': search_errors,
        'direct_cards_technical_errors': direct_errors,
        'links_need_review': links_need_review,
        'inactive_placements': _count(search_summary, 'inactive_placements'),
        'not_expected_placements': _count(search_summary, 'not_expected_placements'),
        'direct_cards_incomplete': direct_incomplete,
        'partial_reasons': reasons,
    }
    if catalogue_failures:
        result['dealer_catalogue_technical_errors'] = catalogue_errors
        result['dealer_catalogue_failures'] = catalogue_failures
    if any(key in direct_cards_summary for key in (
        'skipped_source_unavailable', 'skipped_catalogue_incomplete', 'skipped_unresolved',
    )):
        result['direct_cards_unchecked'] = direct_unchecked
    if any(key in search_summary for key in ('found', 'review_required', 'missed_confirmed', 'missed_uncertain')):
        result['search_observations'] = {key: _count(search_summary, key) for key in (
            'found', 'review_required', 'missed_confirmed', 'missed_uncertain',
        )}
    return result
