"""Replay one saved cycle into an automatic marketing brief, without live I/O."""

import argparse
import hashlib
import json
from pathlib import Path

from app.service.analytical_summary import save_summary, summary_for_report
from app.service.catalogue_visibility import add_catalogue_visibility
from scripts.check_saved_monitoring import check_saved_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--search')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    path = Path(args.report)
    raw = path.read_bytes()
    checked = check_saved_report(path, args.evidence_dir, search_path=args.search)
    if checked['integrity_status'] != 'passed':
        print('MARKETING_SUMMARY_REFUSED saved_evidence_integrity_unverified')
        return 2
    report = json.loads(raw)
    collection = json.loads(Path(args.search).read_text()) if args.search else None
    add_catalogue_visibility(report, args.evidence_dir, collection=collection)
    brief = summary_for_report(report, args.evidence_dir, mode='offline_saved_data')
    brief['base_report_sha256'] = hashlib.sha256(raw).hexdigest()
    if path.read_bytes() != raw:
        raise ValueError('source report changed')
    save_summary(brief, args.output_dir)
    print('MARKETING_SUMMARY_SAVED no_live_scan=true source_writes=false')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
