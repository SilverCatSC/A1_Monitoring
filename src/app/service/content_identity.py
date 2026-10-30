"""Resolve saved content IDs without inventing an advert identity from vehicle data."""

from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.service.evidence import EvidenceAccessError, read_direct_card_evidence
from app.service.placement_identity import (
    PlacementIdError,
    parse_placement_id,
    placement_id_claims_from_description,
    visual_ascii_placement_candidate,
)


def resolve_content_identity(saved, snapshot, *, evidence_dir=None, operator_confirmed_ids=None):
    """Keep exact, corroborated visual alias and explicit prior review distinct.

    Alias recovery is restricted to the first four characters. All numeric
    characters must remain unchanged. Neither price nor VIN is read here.
    """
    card = saved.get('card') or {}
    description = card.get('description') or ''
    claims = placement_id_claims_from_description(description)
    raw = card.get('placement_id_raw')
    result = {
        'unique_id': None,
        'basis': None,
        'status': 'id_missing_or_invalid',
        'raw_claim': raw,
        'description_claims': claims,
    }
    # Exact IDs need the same card-evidence boundary as recovered aliases when
    # verifying persisted reports. The no-root path supports pure parsing only.
    if evidence_dir is not None:
        try:
            read_direct_card_evidence(saved, str(evidence_dir))
        except EvidenceAccessError:
            result['status'] = 'evidence_unverified'
            return result
    exact = card.get('placement_id')
    try:
        exact = parse_placement_id(exact).value if isinstance(exact, str) else None
    except PlacementIdError:
        exact = None
    if exact and not claims:
        # Compatibility for old saves with parsed exact ID but no captured text label.
        result.update(unique_id=exact, basis='exact', status='resolved')
        return result
    normalized = set()
    malformed = False
    for claim in claims:
        try:
            value = parse_placement_id(claim).value
        except PlacementIdError:
            value = visual_ascii_placement_candidate(claim)
        if value is None:
            malformed = True
        else:
            normalized.add(value)
    if len(normalized) > 1 or (exact and normalized and normalized != {exact}) or (claims and malformed):
        result['status'] = 'conflicting_description_ids'
        return result
    if exact and normalized == {exact} and all(c.strip().upper() == exact for c in claims):
        result.update(unique_id=exact, basis='exact', status='resolved')
        return result
    if len(normalized) == 1:
        candidate = next(iter(normalized))
        exact_description = all(c.strip().upper() == candidate for c in claims)
        basis = 'description_exact' if exact_description else 'visual_alias'
    else:
        candidate, basis = None, 'operator_confirmed'
    if saved.get('source') != 'avito':
        return result
    key = canonical_listing_key(EngineType.AVITO, saved.get('url'))
    if candidate is None:
        # This mapping must originate from confirmed_operator_id, not caller inference.
        candidate = (operator_confirmed_ids or {}).get(key) if not claims and not raw else None
    if candidate is None:
        return result
    capture = card.get('description_capture') or {}
    if (
        capture.get('software_truncated') is not False
        or capture.get('sha256') != hashlib.sha256(description.encode()).hexdigest()
    ):
        result['status'] = 'description_integrity_unverified'
        return result
    if not evidence_dir:
        result['status'] = 'evidence_unverified'
        return result
    try:
        manifest = read_direct_card_evidence(saved, str(evidence_dir))
        if (
            manifest.get('source') != 'avito'
            or manifest.get('purpose') != 'direct_card'
            or manifest.get('http_status') != 200
            or manifest.get('requested_url_sha256') != hashlib.sha256(saved['url'].encode()).hexdigest()
            or manifest.get('final_url_sha256') != manifest.get('requested_url_sha256')
            or saved.get('state') != 'active'
        ):
            raise EvidenceAccessError('card evidence scope differs')
    except EvidenceAccessError:
        result['status'] = 'evidence_unverified'
        return result
    rows = [
        (sheet, number, row)
        for sheet in ('avito-feed-new', 'avito-feed-used')
        for number, row in enumerate(
            snapshot.rows_by_sheet.get(sheet, []), snapshot.first_data_rows.get(sheet, 4)
        )
    ]
    matches = [(sheet, number, row) for sheet, number, row in rows if row.get('Id', '').strip() == candidate]
    if len(matches) != 1:
        result['status'] = 'feed_id_missing' if not matches else 'feed_id_duplicate'
        return result
    sheet, number, row = matches[0]
    platform_id = str(row.get('AvitoId') or '').strip()
    platform_matches = [
        (s, n, r)
        for s, n, r in rows
        if str(r.get('AvitoId') or '').strip() and f'avito:{str(r["AvitoId"]).strip()}' == key
    ]
    if platform_id and (
        not platform_id.isdigit() or f'avito:{platform_id}' != key or len(platform_matches) != 1
    ):
        result['status'] = 'platform_id_conflict'
        return result
    if platform_matches and platform_matches[0][2].get('Id', '').strip() != candidate:
        result['status'] = 'platform_id_conflict'
        return result
    if basis == 'operator_confirmed' and (not platform_id or f'avito:{platform_id}' != key):
        result['status'] = 'operator_confirmation_not_corroborated'
        return result
    # Duplicate descriptions may differ in wording. They corroborate an ID only
    # when every retained variant independently claims the same exact ID.
    descriptions = snapshot.field_values(sheet, number, 'Description', row.get('Description') or '')
    if not all(placement_id_claims_from_description(
            BeautifulSoup(text, 'html.parser').get_text(' ', strip=True)) == [candidate] for text in descriptions):
        result['status'] = 'feed_description_id_unverified'
        return result
    result.update(
        unique_id=candidate,
        basis=basis,
        status='resolved',
        feed_sheet=sheet,
        feed_row=number,
        platform_id_verified=bool(platform_id),
        description_sha256=capture['sha256'],
        captured_at_utc=manifest['captured_at'],
    )
    return result
