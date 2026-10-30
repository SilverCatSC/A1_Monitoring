"""Exercise recovery against copied saved evidence, with no browser or database."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.models import EngineType
from app.scraper.base import ListingHit, ScanResult, evidence_manifest_name
from app.service.cycle_recovery import RecoveryJournal
from app.service.evidence import resolve_named_evidence


def replay_sources(report, collection):
    """Select real completed work only; never require an unselected platform."""
    pairs, unavailable = [], []
    selected = set(report.get('catalogue_enumeration_complete', {})) | {
        entry['source'] for entry in collection['results']}
    for source in sorted(selected):
        entry = next((e for e in collection['results']
                      if e['source'] == source and e['result']['complete']), None)
        card = next((c for c in report['observed_cards']
                     if c['source'] == source and c['state'] == 'active'), None)
        if entry is None or card is None:
            unavailable.append(source)
        else:
            pairs.append((source, entry, card))
    if not pairs:
        raise ValueError('no completed search/card pair available for recovery validation')
    return pairs, unavailable


def check_recovery_saved(cycle_id, evidence_dir, output_dir):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', cycle_id):
        raise ValueError('invalid cycle ID')
    root, output = Path(evidence_dir).resolve(), Path(output_dir).resolve()
    if output == root or root in output.parents:
        raise ValueError('validation output must be outside source evidence')
    directory = root / 'cycles' / cycle_id
    paths = [directory / 'placement_reconciliation.json', directory / 'search_collection.json']
    original = [path.read_bytes() for path in paths]
    report, collection = [json.loads(raw) for raw in original]
    if report['cycle_id'] != cycle_id or collection['cycle_id'] != cycle_id:
        raise ValueError('source cycle differs')
    # Validation deliberately uses the historical snapshot's clock. This is
    # not permission to reuse expired observations in a real current run.
    clock = datetime.fromisoformat(report['created_at_utc'])
    local = output / ('replay-' + uuid.uuid4().hex) / 'evidence'
    local.mkdir(parents=True)

    def copy_evidence(stored):
        source = resolve_named_evidence(stored, str(root))
        relative = source.relative_to(root)
        dest = local / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        shutil.copyfile(source.with_name(evidence_manifest_name(source.name)),
                        dest.with_name(evidence_manifest_name(dest.name)))
        return relative.as_posix()

    first = RecoveryJournal(local, 'original', config='offline-replay', now=lambda: clock)
    scopes, cards = [], []
    pairs, unavailable = replay_sources(report, collection)
    for source, entry, saved_card in pairs:
        value = copy.deepcopy(entry['result'])
        for key, stored in list(value['diagnostics'].items()):
            if key.startswith('page_') and key.endswith('_evidence') and isinstance(stored, str):
                value['diagnostics'][key] = copy_evidence(stored)
        value['scanned_at'] = datetime.fromisoformat(value['scanned_at'])
        value['hits'] = [ListingHit(**hit) for hit in value['hits']]
        result = ScanResult(**value)
        scope = [source, entry['filter_id'], entry['url'], entry['version'], entry['pages_limit']]
        first.search(scope, lambda result=result: result)
        scopes.append(scope)
        saved = copy.deepcopy(saved_card)
        saved['evidence'] = copy_evidence(saved['evidence'])
        first.card(EngineType(source), saved['url'], lambda saved=saved: saved)
        cards.append(saved)

    second = RecoveryJournal(local, 'recovered', parent_id='original', config='offline-replay', now=lambda: clock)

    def forbidden():
        raise AssertionError('recovery attempted an unexpected browser visit')

    for scope in scopes:
        second.search(scope, forbidden)
    for saved in cards:
        result = second.card(EngineType(saved['source']), saved['url'], forbidden)
        if result['card'] != saved['card']:
            raise ValueError('card fields changed during recovery')
    if any(path.read_bytes() != raw for path, raw in zip(paths, original, strict=True)):
        raise ValueError('source files changed')
    verification = {'mode': 'offline_real_saved_data', 'source_cycle_id': cycle_id,
                    'sources_checked': [source for source, _, _ in pairs],
                    'sources_without_replayable_pair': unavailable,
                    'recovery': second.summary(), 'live_scan_started': False, 'marketplace_visits': 0,
                    'source_files_unchanged': True, 'test_clock': clock.isoformat(),
                    'current_live_acceptance': 'not_run',
                    'input_sha256': {path.name: hashlib.sha256(raw).hexdigest()
                                     for path, raw in zip(paths, original, strict=True)}}
    output.mkdir(parents=True, exist_ok=True)
    (local.parent / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    return verification


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(check_recovery_saved(args.cycle_id, args.evidence_dir, args.output_dir), indent=2))
