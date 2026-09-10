#!/usr/bin/env python3
"""Create one immutable, bounded input for the two local review agents."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    head_audit = root / 'artifacts' / 'head_table_audits' / 'latest.json'
    if not head_audit.is_file():
        raise RuntimeError('Head-table audit is missing; run scripts/run_head_table_audit_macos.sh first.')
    with urlopen('http://127.0.0.1:18000/api/v1/status/scans/latest', timeout=10) as response:
        scan = json.loads(response.read().decode('utf-8'))
    company_site_audit = root / 'artifacts' / 'company_site_audits' / 'latest.json'
    site_audit = (
        _read_json(company_site_audit)
        if company_site_audit.is_file()
        else {'status': 'not_run', 'reason': 'Company-site audit artifact is missing.'}
    )

    packet = {
        'schema_version': 1,
        'created_at': datetime.now(UTC).isoformat(),
        'contract': {
            'agents_cannot_change_monitoring_data': True,
            'captured_search_card_fields': ['title', 'card_text', 'price', 'url', 'page_number'],
            'captured_direct_card_fields': [
                'title', 'price', 'year', 'vin', 'availability', 'vat_status',
                'description_excerpt', 'status_code', 'reason', 'url',
            ],
            'not_observed_fields': ['complete_photo_gallery', 'complete_options', 'seller_private_data'],
            'absence_rule': 'A partial or technical scan never proves an advertisement absent.',
            'sales_rule': 'Search visibility and direct-card status are independent facts.',
        },
        'scan': scan,
        'head_table_audit': _read_json(head_audit),
        'company_site_audit': site_audit,
    }
    output_dir = root / 'artifacts' / 'agent_reviews'
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
    path = output_dir / f'live_packet_{stamp}.json'
    payload = json.dumps(packet, ensure_ascii=False, indent=2) + '\n'
    path.write_text(payload, encoding='utf-8')
    (output_dir / 'live_packet_latest.json').write_text(payload, encoding='utf-8')
    print(f'LIVE_AGENT_PACKET_READY {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
