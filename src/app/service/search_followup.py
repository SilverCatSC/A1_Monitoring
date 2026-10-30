"""Verified search supplements, bound to an immutable cycle's original evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.scraper.result_scope import avito_model_search_scope_problem
from app.service.artifact_bundle import publish_bundle, verify_bundle
from app.service.catalogue_visibility import add_catalogue_visibility
from app.service.evidence import read_evidence_manifest
from app.service.filters import normalize_search_url
from app.service.targeted_search import targeted_filter


def _read(path, root):
    path = Path(path)
    path.resolve(strict=True).relative_to(root.resolve())
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('symlink in follow-up evidence')
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('invalid follow-up evidence file')
    return path.read_bytes()


def apply_search_followup(report, root, *, original_raw=None, payload=None):
    """Return a dated in-memory overlay; no identity or original files are changed."""
    root = Path(root).resolve()
    base = root / 'cycles' / report['cycle_id']
    directory = base / 'search_followup'
    if payload is None and not directory.exists():
        return
    original_raw = original_raw or _read(base / 'placement_reconciliation.json', root)
    if payload is None:
        verify_bundle(directory, 'ready.json', required=('followup.json',))
        payload = json.loads(_read(directory / 'followup.json', root))
    if (payload.get('schema_version') != 1 or payload.get('cycle_id') != report['cycle_id']
            or payload.get('base_sha256') != hashlib.sha256(original_raw).hexdigest()):
        raise ValueError('follow-up belongs to another report')
    collection = json.loads(_read(base / 'search_collection.json', root))
    if collection.get('cycle_id') != report['cycle_id']:
        raise ValueError('search collection belongs to another cycle')
    records = payload.get('records')
    if not isinstance(records, list) or not records or len(records) > 8:
        raise ValueError('invalid targeted batch')
    seen = set()
    for record in records:
        definition = targeted_filter(record['filter_key'])
        if record['filter_key'] in seen:
            raise ValueError('repeated follow-up filter')
        seen.add(record['filter_key'])
        result = record['result']
        count, limit = result['page_count'], record['pages_limit']
        if (record.get('kind') != 'targeted_search' or record.get('source') != 'avito'
                or record.get('writes_monitoring_cycle') is not False or record.get('scope_errors') != []
                or record['url'] != definition.url or result.get('complete') is not True or result.get('error')
                or type(count) is not int or type(limit) is not int or not 1 <= count <= limit <= 5
                or count < limit and result.get('exhausted') is not True):
            raise ValueError('follow-up search is incomplete or scope differs')
        captured = datetime.fromisoformat(result['scanned_at'])
        original = datetime.fromisoformat(report['created_at_utc'])
        if captured.tzinfo is None or captured < original:
            raise ValueError('follow-up predates the report')
        matches = [e for e in collection['results'] if e['source'] == 'avito'
                   and normalize_search_url(e['url']) == normalize_search_url(record['url'])]
        if len(matches) != 1 or matches[0]['result']['complete'] or matches[0]['pages_limit'] != limit:
            raise ValueError('follow-up must replace only a missing search')
        diagnostics = result['diagnostics']
        for page in range(1, count + 1):
            manifest = read_evidence_manifest(diagnostics.get(f'page_{page}_evidence'), str(root))
            final = diagnostics.get(f'page_{page}_final_url', '')
            if (manifest.get('source') != 'avito' or manifest.get('purpose') != 'search_page'
                    or manifest.get('page_number') != page or manifest.get('http_status') != 200
                    or diagnostics.get(f'page_{page}_http_status') != 200
                    or manifest.get('requested_url_sha256') != hashlib.sha256(record['url'].encode()).hexdigest()
                    or manifest.get('final_url_sha256') != hashlib.sha256(final.encode()).hexdigest()
                    or avito_model_search_scope_problem(record['url'], final)):
                raise ValueError('follow-up page evidence is not verified')
        if any(type(hit.get('page_number')) is not int or not 1 <= hit['page_number'] <= count
               or type(hit.get('position')) is not int or hit['position'] < 1 for hit in result['hits']):
            raise ValueError('follow-up position is outside verified pages')
        matches[0]['result'] = result
    working = copy.deepcopy(report)
    add_catalogue_visibility(working, root, collection=collection)
    for current, revised in zip(report['observed_cards'], working['observed_cards'], strict=True):
        if current['source'] == 'avito':
            current['original_search_positions'] = copy.deepcopy(current.get('search_positions', []))
            current['search_positions'] = revised.get('search_positions', [])
            for position in current['search_positions']:
                entry = next(e for e in collection['results'] if e['filter_id'] == position['filter_id'])
                position['checked_at_utc'] = entry['result']['scanned_at']
                position['post_run'] = any(e['filter_key'] in seen and
                    normalize_search_url(e['url']) == normalize_search_url(entry['url']) for e in records)
    report['search_followup'] = {'completed_filters': len(records),
                                 'created_at_utc': payload['created_at_utc'],
                                 'original_cycle_status_preserved': True,
                                 'source_evidence_unchanged': True}


def save_search_followup(report_path, batch_path, root):
    root = Path(root).resolve()
    raw = _read(report_path, root)
    report = json.loads(raw)
    batch = json.loads(Path(batch_path).read_text())
    if batch.get('kind') != 'targeted_search_batch' or batch.get('writes_monitoring_cycle') is not False:
        raise ValueError('not a targeted-only batch')
    payload = {'schema_version': 1, 'cycle_id': report['cycle_id'],
               'base_sha256': hashlib.sha256(raw).hexdigest(), 'records': batch['records'],
               'created_at_utc': max(r['created_at_utc'] for r in batch['records'])}
    apply_search_followup(report, root, original_raw=raw, payload=payload)
    publish_bundle(Path(report_path).parent / 'search_followup',
                   {'followup.json': json.dumps(payload, ensure_ascii=False)},
                   marker='ready.json', commit_member='followup.json')
    return report
