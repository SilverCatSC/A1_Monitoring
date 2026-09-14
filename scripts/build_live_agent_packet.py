#!/usr/bin/env python3
"""Create a bounded AI packet from one completed, explicitly named cycle."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return value


def _require_cycle_artifact(name: str, artifact: dict, cycle_id: str) -> None:
    if artifact.get('cycle_id') != cycle_id:
        raise ValueError(
            f'{name} does not belong to cycle_id={cycle_id}; rerun the audit with --cycle-id'
        )


def build_packet(
    *,
    cycle_id: str,
    cycle: dict,
    scan: dict,
    head_table_audit: dict,
    company_site_audit: dict,
) -> dict:
    """Fail closed rather than merging `latest` artifacts from different runs."""
    if cycle.get('id') != cycle_id:
        raise ValueError('cycle status response does not match requested cycle_id')
    if cycle.get('status') != 'completed':
        raise ValueError(f'cycle_id={cycle_id} is not completed')
    if scan.get('cycle_id') != cycle_id:
        raise ValueError('scan response does not match requested cycle_id')
    runs = scan.get('runs') if isinstance(scan.get('runs'), list) else []
    if not runs or any(
        not isinstance(run, dict)
        or run.get('cycle_id') != cycle_id
        or run.get('status') != 'success'
        for run in runs
    ):
        raise ValueError('cycle scan is incomplete or contains a non-successful source run')
    _require_cycle_artifact('head-table audit', head_table_audit, cycle_id)
    _require_cycle_artifact('company-site audit', company_site_audit, cycle_id)
    return {
        'schema_version': 2,
        'created_at': datetime.now(UTC).isoformat(),
        'cycle': {
            key: cycle.get(key)
            for key in (
                'id', 'status', 'started_at', 'finished_at', 'network_profile',
                'app_version', 'roster_count', 'roster_sha256', 'manifest_path', 'summary',
            )
        },
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
            'cycle_rule': 'Every input artifact must carry the same completed cycle_id.',
        },
        'scan': scan,
        'head_table_audit': head_table_audit,
        'company_site_audit': company_site_audit,
    }


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        value = json.loads(response.read().decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{url} must return a JSON object')
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--api-url', default='http://127.0.0.1:18000/api/v1')
    parser.add_argument('--head-audit', type=Path, default=Path('artifacts/head_table_audits/latest.json'))
    parser.add_argument('--company-site-audit', type=Path, default=Path('artifacts/company_site_audits/latest.json'))
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/agent_reviews'))
    args = parser.parse_args()

    cycle_id = args.cycle_id.strip()
    if not cycle_id:
        raise ValueError('cycle-id must not be empty')
    api_url = args.api_url.rstrip('/')
    safe_cycle_id = quote(cycle_id, safe='')
    packet = build_packet(
        cycle_id=cycle_id,
        cycle=_get_json(f'{api_url}/status/cycles/{safe_cycle_id}'),
        scan=_get_json(f'{api_url}/status/scans/latest?cycle_id={safe_cycle_id}'),
        head_table_audit=_read_json(args.head_audit),
        company_site_audit=_read_json(args.company_site_audit),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
    path = args.output_dir / f'live_packet_{cycle_id}_{stamp}.json'
    payload = json.dumps(packet, ensure_ascii=False, indent=2) + '\n'
    path.write_text(payload, encoding='utf-8')
    (args.output_dir / 'live_packet_latest.json').write_text(payload, encoding='utf-8')
    print(f'LIVE_AGENT_PACKET_READY cycle_id={cycle_id} file={path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
