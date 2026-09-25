"""A narrow, evidence-backed operator review for an exact-ID republication."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import BUSINESS_TRUSTED_NETWORK_PROFILES, settings
from app.models import ListingReconciliation, MonitoringCycle
from app.scraper.base import canonical_listing_key, evidence_manifest_name, is_marketplace_listing_url
from app.service.evidence import EvidenceAccessError, read_evidence_manifest
from app.service.placement_identity import PlacementIdError, parse_placement_id
from app.service.placement_report import PlacementReportError, read_cycle_placement_report


def exact_republication_candidate(record: ListingReconciliation | None) -> dict | None:
    """Return one exact-ID candidate, never a model/price-only suggestion."""
    if record is None or record.state not in {'review_required', 'removed', 'missing_link'}:
        return None
    if not is_marketplace_listing_url(record.source, record.url):
        return None
    if (record.details or {}).get('network_profile') not in BUSINESS_TRUSTED_NETWORK_PROFILES:
        return None
    candidates = []
    old_key = canonical_listing_key(record.source, record.url)
    for candidate in record.candidates or []:
        if not isinstance(candidate, dict) or candidate.get('id_match_basis') != 'exact':
            continue
        raw_id = candidate.get('placement_id')
        url = candidate.get('url')
        if (not isinstance(raw_id, str) or not isinstance(url, str)
                or not isinstance(candidate.get('id'), str)
                or not isinstance(candidate.get('evidence'), str)
                or not is_marketplace_listing_url(record.source, url)
                or canonical_listing_key(record.source, url) == old_key):
            continue
        try:
            placement_id = parse_placement_id(raw_id).value
        except PlacementIdError:
            continue
        candidates.append({
            'id': candidate['id'], 'placement_id': placement_id,
            'url': url, 'evidence': candidate['evidence'],
            'feed_sheet': candidate.get('feed_sheet'),
            'feed_row': candidate.get('feed_row'),
        })
    return candidates[0] if len(candidates) == 1 else None


def exact_candidate_proof_url(
    db: Session, record: ListingReconciliation, candidate: dict,
) -> str | None:
    """Bind the candidate URL to one intact screenshot in its cycle report."""
    if not record.cycle_id:
        return None
    cycle = db.get(MonitoringCycle, record.cycle_id)
    if cycle is None:
        return None
    try:
        report = read_cycle_placement_report(cycle, settings.evidence_dir)
        manifest = read_evidence_manifest(candidate['evidence'], settings.evidence_dir)
    except (PlacementReportError, EvidenceAccessError):
        return None
    digest = hashlib.sha256(candidate['url'].encode('utf-8')).hexdigest()
    if (manifest.get('source') != record.source.value
            or manifest.get('purpose') != 'direct_card'
            or manifest.get('final_url_sha256') != digest):
        return None
    findings = [
        finding for finding in report['findings']
        if finding.get('code') == 'republication_candidate'
        and finding.get('id_match_basis') == 'exact'
        and finding.get('placement_id') == candidate['placement_id']
        and finding.get('operator_review_check_id') == record.id
        and canonical_listing_key(record.source, finding.get('current_url'))
        == canonical_listing_key(record.source, record.url)
        and len(finding.get('observed_urls', [])) == 1
        and canonical_listing_key(record.source, finding['observed_urls'][0])
        == canonical_listing_key(record.source, candidate['url'])
    ]
    if len(findings) != 1:
        return None
    matches = [
        index for index, card in enumerate(report['observed_cards'])
        if card.get('source') == record.source.value
        and card.get('state') == 'active'
        and canonical_listing_key(record.source, card.get('url'))
        == canonical_listing_key(record.source, candidate['url'])
        and card.get('evidence') == candidate['evidence']
        and card.get('evidence_manifest') == evidence_manifest_name(candidate['evidence'])
    ]
    if len(matches) != 1:
        return None
    return f'/api/v1/status/cycles/{cycle.id}/placement-evidence/{matches[0]}'


def republication_review(
    db: Session, record: ListingReconciliation | None, current_url: str | None,
) -> dict | None:
    candidate = exact_republication_candidate(record)
    if candidate is None or record is None:
        return None
    if canonical_listing_key(record.source, current_url) != canonical_listing_key(record.source, record.url):
        return None
    proof_url = exact_candidate_proof_url(db, record, candidate)
    checked_at = record.checked_at.replace(tzinfo=UTC) if record.checked_at.tzinfo is None else record.checked_at
    fresh = datetime.now(UTC) - checked_at <= timedelta(hours=24)
    return {
        **candidate,
        'check_id': record.id,
        'source': record.source.value,
        'old_url': current_url,
        'checked_at': checked_at,
        'proof_url': proof_url,
        'confirmable': bool(proof_url and fresh),
        'needs_fresh_check': not fresh,
    }
