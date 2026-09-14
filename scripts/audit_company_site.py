#!/usr/bin/env python3
"""Write the read-only A1Auto catalogue audit artifact."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cycle-id', default='')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from app.service.company_site_audit import audit_company_site

    target = root / 'artifacts' / 'company_site_audits'
    target.mkdir(parents=True, exist_ok=True)
    report = audit_company_site()
    if args.cycle_id:
        report['cycle_id'] = args.cycle_id
    stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
    path = target / f'audit_{stamp}.json'
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    path.write_text(payload, encoding='utf-8')
    (target / 'latest.json').write_text(payload, encoding='utf-8')
    print(f'COMPANY_SITE_AUDIT_READY {path}')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
