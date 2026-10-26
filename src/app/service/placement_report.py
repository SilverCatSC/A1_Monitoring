"""Read one cycle-owned placement report without trusting a stored path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path

from app.models import EngineType, MonitoringCycle
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.service.content_review import read_content_review
from app.service.targeted_search import add_reviewed_targeted_positions

MAX_REPORT_BYTES = 4 * 1024 * 1024


def _publication_alerts_valid(alerts, cycle_id):
    if (not isinstance(alerts, dict) or alerts.get('schema_version') != 1
            or alerts.get('cycle_id') != cycle_id or not isinstance(alerts.get('items'), list)
            or len(alerts['items']) > 5000 or alerts.get('total') != len(alerts['items'])
            or not isinstance(alerts.get('baseline_cycle_by_source'), dict)
            or set(alerts['baseline_cycle_by_source']) - {'auto_ru', 'avito'}):
        return False
    for item in alerts['items']:
        if (not isinstance(item, dict) or item.get('source') not in alerts['baseline_cycle_by_source']
                or item.get('kind') not in {'inventory_drop', 'republished', 'republication_candidate',
                    'duplicate_unique_id', 'publication_disappeared', 'publication_inactive',
                    'publication_unverified', 'publication_restored'}
                or not isinstance(item.get('title'), str) or len(item['title']) > 4000):
            return False
        source = EngineType(item['source'])
        urls = item.get('urls', [])
        if not isinstance(urls, list) or len(urls) > 100:
            return False
        urls = urls + [item.get('old_url'), item.get('new_url')]
        if any(url is not None and (not isinstance(url, str)
               or not is_marketplace_listing_url(source, url)) for url in urls):
            return False
    return alerts.get('counts') == dict(Counter(item['kind'] for item in alerts['items']))


class PlacementReportError(ValueError):
    pass


def read_cycle_placement_report(cycle: MonitoringCycle, evidence_dir: str) -> dict:
    """Return only a report created for this exact cycle, or fail closed."""
    cycle_id = str(cycle.id)
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', cycle_id):
        raise PlacementReportError('invalid_cycle_id')
    original = Path('cycles') / cycle_id / 'placement_reconciliation.json'
    reviewed = Path('cycles') / cycle_id / 'placement_reconciliation_reviewed.json'
    summary = cycle.summary or {}
    placement = summary.get('placement_reconciliation') if isinstance(summary, dict) else None
    if not isinstance(placement, dict) or placement.get('report_path') not in {original.as_posix(), reviewed.as_posix()}:
        raise PlacementReportError('report_not_available')
    expected = Path(placement['report_path'])
    root = Path(evidence_dir).resolve()
    target = root / expected
    for path in (root / 'cycles', root / 'cycles' / cycle_id, target):
        if path.is_symlink():
            raise PlacementReportError('invalid_report_path')
    try:
        target.resolve(strict=True).relative_to(root)
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES:
            raise PlacementReportError('invalid_report_file')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, 'rb') as source:
            raw = source.read(MAX_REPORT_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise PlacementReportError('report_unreadable') from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise PlacementReportError('invalid_report_file')
    try:
        report = json.loads(raw.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PlacementReportError('invalid_report_json') from exc
    if (not isinstance(report, dict) or report.get('schema_version') != 1
            or report.get('cycle_id') != cycle_id
            or not isinstance(report.get('findings'), list)
            or not isinstance(report.get('observed_cards'), list)):
        raise PlacementReportError('invalid_report_schema')
    if expected == reviewed:
        original_file = root / original
        review = report.get('identity_review', {})
        if (not isinstance(review, dict) or original_file.is_symlink() or not original_file.is_file()
                or original_file.stat().st_size > MAX_REPORT_BYTES
                or report.get('original_report_path') != original.as_posix()
                or review.get('cycle_id') != cycle_id
                or review.get('original_report_sha256') != hashlib.sha256(original_file.read_bytes()).hexdigest()):
            raise PlacementReportError('invalid_identity_review')
    findings = report['findings']
    cards = report['observed_cards']
    if report.get('publication_alerts') is not None and not _publication_alerts_valid(report['publication_alerts'], cycle_id):
        raise PlacementReportError('invalid_publication_alerts')
    if (len(findings) > 5000 or len(cards) > 1000
            or any(not isinstance(item, dict) or not isinstance(item.get('code'), str)
                   or not isinstance(item.get('observed_urls'), list)
                   or any(not isinstance(url, str) for url in item['observed_urls'])
                   for item in findings)
            or any(not isinstance(item, dict) or not isinstance(item.get('url'), str)
                   or not isinstance(item.get('source'), str)
                   for item in cards)):
        raise PlacementReportError('invalid_report_schema')
    business_review = summary.get('post_run_business_review')
    if business_review is not None:
        if (not isinstance(business_review, dict)
                or business_review.get('schema_version') != 1
                or business_review.get('cycle_id') != cycle_id
                or business_review.get('base_report_sha256') != hashlib.sha256(raw).hexdigest()
                or not isinstance(business_review.get('entries'), list)
                or len(business_review['entries']) > 50
                or any(not isinstance(item, dict)
                       or any(not isinstance(item.get(field), str) or len(item[field]) > 4000
                              for field in ('title', 'status', 'explanation', 'action', 'basis'))
                       for item in business_review['entries'])
                or not isinstance(business_review.get('vclass_cards'), list)
                or len(business_review['vclass_cards']) > 10
                or any(not isinstance(item, dict)
                       or not is_marketplace_listing_url(EngineType.AVITO, item.get('url'))
                       or any(not isinstance(item.get(field), str) or len(item[field]) > 1000
                              for field in ('placement_id', 'design', 'mapping'))
                       for item in business_review['vclass_cards'])):
            raise PlacementReportError('invalid_business_review')
        report['business_review'] = business_review
        reviewed_alerts = business_review.get('publication_alerts')
        if reviewed_alerts is not None:
            if not _publication_alerts_valid(reviewed_alerts, cycle_id):
                raise PlacementReportError('invalid_publication_alert_review')
            # A targeted source review must not erase other marketplaces' signals
            # or replace historical cards, findings, evidence indices or status.
            old_alerts = report.get('publication_alerts', {})
            sources = set(reviewed_alerts['baseline_cycle_by_source'])
            items = [a for a in old_alerts.get('items', []) if a['source'] not in sources]
            items += reviewed_alerts['items']
            report['publication_alerts'] = {**reviewed_alerts, 'items': items,
                'total': len(items), 'counts': dict(Counter(a['kind'] for a in items)),
                'baseline_cycle_by_source': {**old_alerts.get('baseline_cycle_by_source', {}),
                                             **reviewed_alerts['baseline_cycle_by_source']}}
            report['publication_alert_reviewed_at_utc'] = business_review.get(
                'publication_alert_reviewed_at_utc', business_review.get('reviewed_at_utc'))
        confirmed = business_review.get('confirmed_registry_bindings', [])
        if (not isinstance(confirmed, list) or len(confirmed) > 50
                or any(not isinstance(item, dict)
                       or not isinstance(item.get('placement_id'), str)
                       or not isinstance(item.get('listing_id'), str)
                       or not is_marketplace_listing_url(EngineType.AVITO, item.get('url'))
                       for item in confirmed)):
            raise PlacementReportError('invalid_business_review')
        for finding in findings:
            if finding['code'] != 'vehicle_anchor_missing':
                continue
            for binding in confirmed:
                if (finding.get('placement_id') == binding['placement_id']
                        and any(canonical_listing_key(EngineType.AVITO, url)
                                == canonical_listing_key(EngineType.AVITO, binding['url'])
                                for url in finding['observed_urls'])):
                    basis = binding.get('basis', 'owner_confirmed_registry_binding')
                    if basis not in {'owner_confirmed_registry_binding', 'source_verified_registry_binding'}:
                        raise PlacementReportError('invalid_business_review')
                    finding['post_run_resolution'] = basis
                    finding['confirmed_registry_listing_id'] = binding['listing_id']
                    finding['confirmed_registry_url'] = binding['url']
        add_reviewed_targeted_positions(report, evidence_dir)
        content = read_content_review(report, evidence_dir)
        if content is not None:
            report['content_review'] = content
    return report
