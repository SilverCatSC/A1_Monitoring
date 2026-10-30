"""Separate search-only evidence; never mutates a previous monitoring cycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.scraper.result_scope import avito_model_search_scope_problem
from app.service.filters import CANONICAL_FILTERS
from app.service.placement_identity import placement_id_from_description

TARGETED_FILTER_KEYS = tuple(item.key for item in CANONICAL_FILTERS if item.source == EngineType.AVITO)


def targeted_keys(value):
    keys = tuple(value.split(','))
    if not keys or len(keys) != len(set(keys)) or any(k not in TARGETED_FILTER_KEYS for k in keys):
        raise ValueError('unsupported or repeated targeted search filter')
    return keys


def targeted_filter(key):
    if key not in TARGETED_FILTER_KEYS:
        raise ValueError('unsupported targeted search filter')
    return next(item for item in CANONICAL_FILTERS if item.key == key)


def save_targeted_search(key, result, *, pages, output_path, evidence_dir):
    definition = targeted_filter(key)
    if not 1 <= pages <= 5:
        raise ValueError('targeted search depth must be between one and five pages')
    # A redirect to a broader result set cannot establish a model's position.
    diagnostics = result.get('diagnostics') or {}
    scope_errors = []
    attempted = {int(match[1]) for key in diagnostics
                 if (match := re.fullmatch(r'page_(\d+)_final_url', key))}
    attempted.update(range(1, int(result.get('page_count', 0)) + 1))
    for page in sorted(attempted):
        if avito_model_search_scope_problem(
                definition.url, str(diagnostics.get(f'page_{page}_final_url') or '')):
            scope_errors.append(f'page_{page}_filter_scope_unverified')
    if result.get('complete') and not result.get('page_count'):
        scope_errors.append('no_search_page_verified')
    reviewed = dict(result)
    if scope_errors:
        reviewed['complete'] = False
        reviewed['error'] = 'targeted_filter_scope_unverified'
    record = {
        'schema_version': 1, 'kind': 'targeted_search', 'record_id': str(uuid4()),
        'created_at_utc': datetime.now(UTC).isoformat(),
        'filter_key': key, 'filter_name': definition.name, 'source': definition.source.value,
        'url': definition.url, 'pages_limit': pages, 'evidence_dir': str(Path(evidence_dir).resolve()),
        'network_profile': os.environ.get('NETWORK_PROFILE', 'unknown'), 'writes_monitoring_cycle': False,
        'scope_errors': scope_errors, 'result': reviewed,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with target.open('x', encoding='utf-8') as out:
        json.dump(record, out, ensure_ascii=False, indent=2)
    target.chmod(0o600)
    return record


def add_reviewed_targeted_positions(report, evidence_dir):
    """Show dated positive hits without rewriting the cycle's original search."""
    business = report.get('business_review') or {}
    review = business.get('voyah_search_review')
    if (not isinstance(review, dict) or review.get('complete') is not True
            or review.get('cycle_id') != report['cycle_id']
            or not isinstance(review.get('result_copy'), str)):
        return
    relative = Path(review.get('result_copy', ''))
    expected_parent = Path('cycles') / report['cycle_id'] / 'business_review'
    if relative.is_absolute() or relative.parent != expected_parent:
        return
    root = Path(evidence_dir).resolve()
    target = root / relative
    if any((root / Path(*relative.parts[:length])).is_symlink()
           for length in range(1, len(relative.parts) + 1)):
        return
    try:
        if not target.is_file() or target.stat().st_size > 4 * 1024 * 1024:
            return
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != review.get('source_sha256'):
            return
        record = json.loads(raw)
        if (record.get('schema_version') != 1 or record.get('kind') != 'targeted_search'
                or record.get('record_id') != review.get('record_id')
                or record.get('source') != 'avito' or record.get('writes_monitoring_cycle') is not False
                or record.get('scope_errors') != []):
            return
        definition = targeted_filter(record.get('filter_key'))
        timestamp = record['created_at_utc']
        if datetime.fromisoformat(timestamp).tzinfo is None:
            return
        result = record['result']
        pages = result['page_count']
        if (result.get('complete') is not True or result.get('error')
                or type(pages) is not int or not 1 <= pages <= 5
                or record.get('url') != definition.url):
            return
        diagnostics = result['diagnostics']
        positions = {}
        for binding in business.get('confirmed_registry_bindings', []):
            key = canonical_listing_key(EngineType.AVITO, binding['url'])
            hits = [hit for hit in result['hits']
                    if canonical_listing_key(EngineType.AVITO, hit['url']) == key
                    and placement_id_from_description((hit.get('raw') or {}).get('raw_text'))
                    == binding['placement_id']]
            if not hits:
                continue
            hit = min(hits, key=lambda item: (item['page_number'], item['position']))
            page, position = hit['page_number'], hit['position']
            if (type(page) is not int or not 1 <= page <= pages
                    or type(position) is not int or position < 1
                    or diagnostics.get(f'page_{page}_http_status') != 200
                    or avito_model_search_scope_problem(
                        record['url'], diagnostics.get(f'page_{page}_final_url', ''))):
                continue
            positions[key] = {
                'filter_name': definition.name, 'placement_id': binding['placement_id'],
                'label': f'Стр. {page}, место {position}', 'page': page, 'position': position,
                'checked_at_utc': timestamp, 'record_id': record['record_id'],
                'result_copy': relative.as_posix(), 'source_sha256': review['source_sha256'],
                'post_run': True,
            }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    for card in report['observed_cards']:
        if card['source'] == 'avito' and card.get('state') == 'active':
            position = positions.get(canonical_listing_key(EngineType.AVITO, card['url']))
            if position:
                card['supplemental_search_positions'] = [position]
