"""Owner-defined placement expectations, separate from transport evidence."""
from app.scraper.base import canonical_listing_key, evidence_manifest_name

LABELS = {
    'not_expected': 'Размещение не заявлено — нет ссылки',
    'inactive': 'Объявление неактивно на площадке',
}


def resolved_placement_status(source, current_url, checked_url, details):
    """Resolve only no-placement or complete-catalogue absence facts.

    A nonempty invalid URL, ambiguous identity, stale check, CAPTCHA or partial
    catalogue remains unresolved. Never infer why a publication is inactive.
    """
    if not str(current_url or '').strip():
        return 'not_expected'
    key = canonical_listing_key(source, current_url)
    direct = details.get('direct_inspection') or {}
    evidence = direct.get('evidence')
    if (key and key == canonical_listing_key(source, checked_url)
            and direct.get('state') == 'removed'
            and direct.get('status_code') in {'sold', 'closed', 'unpublished'}
            and isinstance(evidence, str) and bool(evidence.strip())
            and direct.get('evidence_manifest') == evidence_manifest_name(evidence)):
        # A fresh, evidenced explicit removal at this URL supersedes the
        # earlier catalogue snapshot. This never establishes a vehicle sale.
        return 'inactive'
    if (key and key == canonical_listing_key(source, checked_url)
            and details.get('catalogue_complete') is True
            and (details.get('direct_inspection') or {}).get('state') not in {'active', 'blocked', 'unknown'}
            and details.get('inspection_skip_reason') == 'not_in_active_catalogue'):
        return 'inactive'
    return None


def record_placement_status(record, current_url):
    from app.service.republication_review import exact_republication_candidate
    if exact_republication_candidate(record):
        return None
    # A same-ID replacement found in a diagnostic cycle or via a validated
    # visual alias also prevents calling the placement inactive. Keep its link
    # review pending until the replacement is accepted.
    for candidate in record.candidates or []:
        if (candidate.get('id_match_basis') in {'exact', 'visual_alias'}
                and candidate.get('placement_id') and candidate.get('evidence')
                and canonical_listing_key(record.source, candidate.get('url'))
                and canonical_listing_key(record.source, candidate.get('url')) != canonical_listing_key(record.source, current_url)):
            return None
    return resolved_placement_status(record.source, current_url, record.url, record.details or {})


def outside_search_label(observation, record):
    """5+ is a search-depth label for an active publication, not its rank."""
    if not observation or not record or observation.state.value not in {'absent_uncertain', 'absent_confirmed'}:
        return None
    if not record.cycle_id or record.cycle_id != observation.scan_run.cycle_id:
        return None
    direct = (record.details or {}).get('direct_inspection') or {}
    if direct.get('state') != 'active':
        return None
    expected = (observation.raw_payload or {}).get('expected_listing_key')
    if not expected or expected != canonical_listing_key(record.source, record.url):
        return None
    diagnostics = (observation.raw_payload or {}).get('scan_diagnostics') or {}
    # Historical complete results have page state evidence; new results also
    # persist the configured depth and explicit completeness flag.
    complete = diagnostics.get('search_complete') is True or (
        'search_complete' not in diagnostics and diagnostics.get('page_5_state') == 'results'
        and not diagnostics.get('error'))
    if not complete:
        return None
    return f"{diagnostics.get('search_pages_limit', 5)}+"
