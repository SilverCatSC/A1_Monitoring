"""Account for unfinished work against one sealed monitoring-cycle roster."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.models import (
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
    ScanRun,
)
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.scraper.seller import SELLER_SOURCES


class PendingChecksError(RuntimeError):
    pass


def _latest(rows, key_fields, time_field):
    latest = {}
    for row in rows:
        key = tuple(getattr(row, field) for field in key_fields)
        previous = latest.get(key)
        if previous is None or (getattr(row, time_field), row.id) > (
            getattr(previous, time_field), previous.id
        ):
            latest[key] = row
    return latest


def _blocked_reason(diagnostics: dict) -> tuple[str, str | None]:
    for key, value in diagnostics.items():
        if key.endswith('_http_status') and value == 429:
            return 'http_429', diagnostics.get(key.replace('_http_status', '_evidence'))
    error = str(diagnostics.get('error') or '').lower()
    if 'http 429' in error:
        return 'http_429', None
    captcha_page = next((key.removesuffix('_final_url') for key, value in diagnostics.items()
                         if key.endswith('_final_url') and 'captcha' in str(value).lower()), None)
    if 'captcha' in error or captcha_page:
        evidence = diagnostics.get(f'{captcha_page}_evidence') if captcha_page else None
        return 'captcha', evidence
    return 'technical_error', None


def _blocked_page(diagnostics: dict) -> int | None:
    for key, value in diagnostics.items():
        match = re.fullmatch(r'page_(\d+)_http_status', key)
        if match and value == 429:
            return int(match.group(1))
    for key, value in diagnostics.items():
        match = re.fullmatch(r'page_(\d+)_final_url', key)
        if match and 'captcha' in str(value).lower():
            return int(match.group(1))
    match = re.search(r'\bpage (\d+)\b', str(diagnostics.get('error') or ''), re.I)
    return int(match.group(1)) if match else None


def build_pending_checks(db, *, cycle_id: str, roster: list[dict], selected_sources: set[str],
                         placement: dict | None = None, placement_report: dict | None = None) -> list[dict]:
    """Return deterministic pending work; an unobserved roster item is never success."""
    items = []
    discovery = db.query(DealerDiscoveryRun).filter_by(cycle_id=cycle_id).all()
    discovery_by_url = {(run.source.value, run.dealer_url): run for run in discovery}
    for source in sorted(selected_sources):
        for dealer_url in SELLER_SOURCES.get(source, []):
            run = discovery_by_url.get((source, dealer_url))
            if run is None or not run.complete:
                reason, evidence = _blocked_reason({**(run.diagnostics or {}), 'error': run.error or ''}) if run else ('not_run', None)
                items.append({'phase': 'dealer_catalogue', 'source': source,
                              'reason': 'incomplete' if run and reason == 'technical_error' else reason,
                              'dealer_url': dealer_url, 'run_id': run.id if run else None,
                              'evidence': evidence})

    runs = db.query(ScanRun).filter_by(cycle_id=cycle_id).all()
    run_ids = [run.id for run in runs]
    observations = (db.query(ListingObservation).filter(ListingObservation.run_id.in_(run_ids)).all()
                    if run_ids else [])
    search_by_key = _latest(observations, ('listing_id', 'filter_id', 'source'), 'observed_at')
    direct_rows = db.query(ListingReconciliation).filter_by(cycle_id=cycle_id).all()
    direct_by_key = _latest(direct_rows, ('listing_id', 'source'), 'checked_at')

    for listing in roster:
        listing_id = listing['listing_id']
        for entry in listing.get('filters', []):
            source = entry['source']
            if source not in selected_sources:
                continue
            observation = search_by_key.get((listing_id, entry['id'], EngineType(source)))
            observed_key = ((observation.raw_payload or {}).get('expected_listing_key')
                            if observation else None)
            observed_version = ((observation.raw_payload or {}).get('filter_version')
                                if observation else None)
            roster_key = listing.get('sources', {}).get(source)
            mismatch = (observation is not None and
                        (observed_key != roster_key or observed_version != entry['version']))
            if observation is None or mismatch or observation.state in {
                ObservationState.TECHNICAL_ERROR, ObservationState.REVIEW_REQUIRED,
            }:
                diagnostics = ((observation.raw_payload or {}).get('scan_diagnostics') or {}) if observation else {}
                reason, evidence = _blocked_reason(diagnostics)
                if observation is None:
                    reason = 'not_run'
                elif mismatch:
                    reason = 'roster_mismatch'
                elif observation.state == ObservationState.REVIEW_REQUIRED:
                    reason = 'review_required'
                elif diagnostics.get('blocked_by_filter_id'):
                    reason = 'blocked_after_prior_filter'
                items.append({'phase': 'search', 'source': source, 'listing_id': listing_id,
                              'filter_id': entry['id'], 'filter_version': entry['version'],
                              'reason': reason, 'observation_id': observation.id if observation else None,
                              'blocked_page': _blocked_page(diagnostics),
                              'blocked_by_filter_id': diagnostics.get('blocked_by_filter_id'),
                              'evidence': evidence})
        for source in sorted(selected_sources):
            key = listing.get('sources', {}).get(source)
            record = direct_by_key.get((listing_id, EngineType(source)))
            inspection = ((record.details or {}).get('direct_inspection') or {}) if record else {}
            evidence = inspection.get('evidence')
            proven = (
                inspection.get('state') in {'active', 'removed'}
                and isinstance(evidence, str)
                and inspection.get('evidence_manifest') == evidence_manifest_name(evidence)
                and canonical_listing_key(EngineType(source), record.url) == key
            )
            if not proven:
                reason = ('link_missing' if not key else
                          'roster_mismatch' if record and canonical_listing_key(EngineType(source), record.url) != key
                          else inspection.get('status_code') or inspection.get('state') or 'not_run')
                items.append({'phase': 'direct_card', 'source': source, 'listing_id': listing_id,
                              'listing_key': key, 'reason': reason,
                              'reconciliation_id': record.id if record else None,
                              'evidence': evidence})

    if placement is not None:
        coverage = (placement_report or {}).get('catalogue_complete') or {}
        for source in sorted(selected_sources):
            if coverage.get(source) is not True:
                items.append({'phase': 'identity_coverage', 'source': source,
                              'reason': 'incomplete', 'report_path': placement.get('report_path')})
        if placement_report is not None:
            observed = {
                (card.get('source'), canonical_listing_key(
                    EngineType(card['source']), card.get('url'))): card
                for card in placement_report.get('observed_cards', [])
                if card.get('source') in selected_sources
            }
            candidates = db.query(DealerListingCandidate).all()
            current_runs = {run.id for run in discovery}
            for candidate in candidates:
                if (candidate.source.value not in selected_sources
                        or (candidate.raw_payload or {}).get('discovery_run_id') not in current_runs):
                    continue
                key = canonical_listing_key(candidate.source, candidate.listing_url)
                card = observed.get((candidate.source.value, key))
                card_evidence = card.get('evidence') if card else None
                if (card is None or card.get('state') != 'active'
                        or not isinstance(card_evidence, str)
                        or card.get('evidence_manifest') != evidence_manifest_name(card_evidence)):
                    items.append({'phase': 'identity_card', 'source': candidate.source.value,
                                  'listing_key': key,
                                  'reason': 'not_inspected' if card is None else 'not_verified',
                                  'candidate_id': candidate.id})

    return sorted(items, key=lambda item: (
        item['source'], item['phase'], item.get('listing_id') or '',
        item.get('filter_id') or '', item.get('listing_key') or '',
        item.get('dealer_url') or '',
    ))


def pending_summary(items: list[dict], path: str) -> dict:
    return {'count': len(items), 'by_phase': dict(sorted(Counter(
        item['phase'] for item in items
    ).items())), 'report_path': path}


def load_cycle_roster(evidence_dir: Path, manifest_path: str, cycle_id: str,
                      expected_sha256: str | None = None) -> list[dict]:
    target = (evidence_dir / manifest_path).resolve()
    if not target.is_relative_to(evidence_dir.resolve()) or not target.is_file():
        raise PendingChecksError('sealed cycle roster is unavailable')
    try:
        manifest = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PendingChecksError('sealed cycle roster cannot be read') from exc
    if not isinstance(manifest, dict) or manifest.get('cycle_id') != cycle_id or not isinstance(manifest.get('roster'), list):
        raise PendingChecksError('sealed cycle roster does not match cycle')
    roster_hash = hashlib.sha256(json.dumps(
        manifest['roster'], ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    if roster_hash != manifest.get('roster_sha256'):
        raise PendingChecksError('sealed cycle roster hash mismatch')
    if expected_sha256 is not None and roster_hash != expected_sha256:
        raise PendingChecksError('sealed cycle roster differs from ledger hash')
    return manifest['roster']


def load_placement_report(evidence_dir: Path, placement: dict | None) -> dict | None:
    if placement is None or not placement.get('report_path'):
        return None
    path = (evidence_dir / placement['report_path']).resolve()
    if not path.is_relative_to(evidence_dir.resolve()) or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
