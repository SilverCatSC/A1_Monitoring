"""Reparse captured workbook CSVs and compare a saved cycle without browsing.

Publishes a separate content-review bundle. It never edits the original cycle,
contacts a source, changes ad identities or updates the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import Path

import httpx

from app.service.analytical_summary import render_marketing_text, summary_for_report
from app.service.artifact_bundle import publish_bundle
from app.service.automatic_content import automatic_content_review
from app.service.marketing_content import add_marketing_comparison
from app.service.placement_feed_snapshot import FEED_SPECS, read_placement_feed_snapshot
from scripts.check_saved_monitoring import check_saved_report


def review_saved_cycle(cycle_id, evidence_dir, csv_dir, *, stem, db=None):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', cycle_id) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', stem):
        raise ValueError('invalid cycle or output name')
    root, captured = Path(evidence_dir).resolve(), Path(csv_dir).resolve()
    path = root / 'cycles' / cycle_id / 'placement_reconciliation.json'
    original = path.read_bytes()
    if check_saved_report(path, root)['integrity_status'] != 'passed':
        raise ValueError('saved card evidence did not pass verification')
    report = json.loads(original)
    metadata = json.loads((captured / 'capture_metadata.json').read_text())
    names = {str(spec[0]): sheet for sheet, spec in FEED_SPECS.items()}
    names['0'] = 'Размещение_автомобилей'
    inputs = {}
    for gid, sheet in names.items():
        raw = (captured / (sheet + '.csv')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata['sha256_by_sheet'][sheet]:
            raise ValueError('captured CSV changed: ' + sheet)
        inputs[gid] = raw

    def serve(request):
        return httpx.Response(200, content=inputs[request.url.params['gid']], headers={'content-type': 'text/csv'})

    workbook = report['automatic_content_checks']['marketing_comparison']['workbook_url']
    # MockTransport cannot contact Google or any other network service.
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        snapshot = read_placement_feed_snapshot(workbook, client=client)
    snapshot = replace(snapshot, read_started_at_utc=metadata['read_started_at_utc'],
                       read_finished_at_utc=metadata['read_finished_at_utc'])
    content = automatic_content_review(report, root)
    confirmations = {}
    if db is not None:
        from app.models import EngineType
        from app.scraper.base import canonical_listing_key
        from app.service.identity_review import confirmed_operator_id

        for saved in report['observed_cards']:
            if saved.get('source') != 'avito':
                continue
            uid = confirmed_operator_id(db, root, saved['url'], saved)
            if uid:
                confirmations[canonical_listing_key(EngineType.AVITO, saved['url'])] = uid
    add_marketing_comparison(content, report['observed_cards'], snapshot, evidence_dir=root,
                            operator_confirmed_ids=confirmations)
    digest = hashlib.sha256(original).hexdigest()
    content['feed_block_review'] = {'base_report_sha256': digest,
                                   'mode': 'saved_cards_reparsed_captured_workbook',
                                   'workbook_capture': metadata}
    if confirmations:
        content['feed_block_review']['reused_owner_confirmations'] = confirmations
    report['content_review'] = content
    brief = summary_for_report(report, root, mode='offline_saved_cards_reparsed_feeds')
    directory = path.parent / 'content_review'
    review_name = stem + '.json'
    verification = {'cycle_id': cycle_id, 'new_live_scan': False, 'source_writes': False,
                    'base_report_sha256': digest, 'workbook_sha256': metadata['sha256_by_sheet'],
                    'before_marketing_bindings': report['automatic_content_checks']['summary']['marketing_bindings'],
                    'after_marketing_bindings': content['summary']['marketing_bindings'],
                    'summary_counts': brief['counts']}
    if path.read_bytes() != original:
        raise ValueError('original cycle changed during review')
    def encode(obj):
        return json.dumps(obj, ensure_ascii=False, indent=2) + '\n'

    review_text = encode(content)
    publish_bundle(directory, {
        review_name: review_text,
        stem + '_snapshot.json': encode(asdict(snapshot)),
        stem + '_summary.json': encode(brief),
        stem + '_marketing.txt': render_marketing_text(brief),
        stem + '_verification.json': encode(verification),
    }, marker=stem + '.ready.json', commit_member=review_name)
    reference = {'report_path': (directory / review_name).relative_to(root).as_posix(),
                 'sha256': hashlib.sha256(review_text.encode()).hexdigest()}
    return {'cycle_id': cycle_id, 'base_report_sha256': digest, 'content_review': reference,
            'verification': verification}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--csv-dir', required=True)
    parser.add_argument('--output-stem', required=True)
    args = parser.parse_args()
    result = review_saved_cycle(args.cycle_id, args.evidence_dir, args.csv_dir, stem=args.output_stem)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
