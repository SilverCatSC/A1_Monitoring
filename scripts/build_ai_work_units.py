#!/usr/bin/env python3
"""Split the immutable monitoring packet into small per-vehicle AI work units."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return value


def _text(value: Any, limit: int = 2200) -> str | None:
    clean = ' '.join(str(value or '').split())
    return clean[:limit] or None


def _trim_mapping(value: Any, *, text_limit: int = 2200) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str):
            result[str(key)] = _text(item, text_limit)
        elif isinstance(item, (dict, list, int, float, bool)) or item is None:
            result[str(key)] = item
    return result


def _scan_summary(scan: dict[str, Any]) -> dict[str, Any]:
    runs = []
    for run in scan.get('runs', []):
        if not isinstance(run, dict):
            continue
        runs.append({key: run.get(key) for key in (
            'id', 'source', 'status', 'network_profile', 'started_at', 'finished_at',
            'filters_total', 'filters_ok', 'pages_scanned', 'technical_errors',
            'observations', 'state_counts', 'evidence_files', 'evidence_pages',
        )})
    return {
        'requested_pages': scan.get('requested_pages'),
        'network_profile': scan.get('network_profile'),
        'latest_worker_profile': scan.get('latest_worker_profile'),
        'runs': runs,
    }


def _safe_image(evidence_root: Path, value: Any) -> Path | None:
    name = Path(str(value or '')).name
    if not name.lower().endswith(('.png', '.jpg', '.jpeg')):
        return None
    candidate = (evidence_root / name).resolve()
    if candidate.parent != evidence_root.resolve() or not candidate.is_file():
        return None
    return candidate


def build_units(packet: dict[str, Any], evidence_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    head = packet.get('head_table_audit') if isinstance(packet.get('head_table_audit'), dict) else {}
    site = packet.get('company_site_audit') if isinstance(packet.get('company_site_audit'), dict) else {}
    grouped: dict[str, dict[str, Any]] = {}

    def bucket(row: dict[str, Any]) -> dict[str, Any] | None:
        key = _text(row.get('vehicle_key'), 220)
        if not key:
            return None
        entry = grouped.setdefault(key, {
            'vehicle_key': key,
            'vin': row.get('vin') or None,
            'head_issues': [],
            'content_samples': [],
            'direct_card_samples': [],
            'company_site_issues': [],
        })
        if not entry['vin'] and row.get('vin'):
            entry['vin'] = row['vin']
        return entry

    for row in head.get('issues', []):
        if isinstance(row, dict) and (entry := bucket(row)) is not None:
            entry['head_issues'].append(_trim_mapping(row))
    for row in head.get('content_samples', []):
        if isinstance(row, dict) and (entry := bucket(row)) is not None:
            entry['content_samples'].append(_trim_mapping(row))
    for row in head.get('direct_card_samples', []):
        if isinstance(row, dict) and (entry := bucket(row)) is not None:
            entry['direct_card_samples'].append(_trim_mapping(row))
    for row in site.get('issues', []):
        if isinstance(row, dict) and (entry := bucket(row)) is not None:
            entry['company_site_issues'].append(_trim_mapping(row))

    global_context = {
        'cycle': packet.get('cycle', {}),
        'contract': packet.get('contract', {}),
        'scan': _scan_summary(packet.get('scan', {})),
        'head_table_summary': head.get('summary', {}),
        'company_site_summary': site.get('summary', {}),
    }
    units: list[dict[str, Any]] = []
    for vehicle_key in sorted(grouped):
        data = grouped[vehicle_key]
        unit_hash = hashlib.sha256(vehicle_key.encode('utf-8')).hexdigest()[:12]
        images = []
        seen = set()
        for sample in [*data['direct_card_samples'], *data['content_samples']]:
            image = _safe_image(evidence_root, sample.get('card_evidence'))
            if image is None or str(image) in seen:
                continue
            seen.add(str(image))
            images.append({
                'path': str(image),
                'file': image.name,
                'source': sample.get('source'),
                'page_number': sample.get('page_number'),
                'listing_url': sample.get('listing_url'),
                'image_kind': 'direct_card' if sample in data['direct_card_samples'] else 'search_card',
                'expected': {
                    'title': sample.get('title'),
                    'price': sample.get('card_price') or (sample.get('card') or {}).get('price'),
                    'head': sample.get('head'),
                    'direct_status': sample.get('status_code'),
                    'vat_status': (sample.get('card') or {}).get('vat_status'),
                },
            })
        units.append({'unit_id': unit_hash, 'data': data, 'images': images})
    return global_context, units


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--packet', type=Path, default=Path('artifacts/agent_reviews/live_packet_latest.json'))
    parser.add_argument('--evidence-dir', type=Path, default=Path('artifacts/evidence'))
    parser.add_argument('--output-root', type=Path, default=Path('artifacts/agent_work_units'))
    args = parser.parse_args()

    packet = _read(args.packet)
    global_context, units = build_units(packet, args.evidence_dir)
    stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
    run_dir = args.output_root / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_units = []
    for unit in units:
        unit_path = run_dir / f"unit_{unit['unit_id']}.json"
        unit_path.write_text(json.dumps({
            'schema_version': 1,
            'global_context': global_context,
            'vehicle': unit['data'],
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        manifest_units.append({
            'unit_id': unit['unit_id'],
            'vehicle_key': unit['data']['vehicle_key'],
            'vin': unit['data']['vin'],
            'unit_path': str(unit_path.resolve()),
            'images': unit['images'],
        })
    manifest = {
        'schema_version': 1,
        'created_at': datetime.now(UTC).isoformat(),
        'cycle_id': (packet.get('cycle') or {}).get('id'),
        'source_packet': str(args.packet.resolve()),
        'run_dir': str(run_dir.resolve()),
        'resource_policy': {
            'parallel_model_requests': 1,
            'unit_scope': 'one vehicle',
            'vision_scope': 'one saved card image',
        },
        'global_context': global_context,
        'units': manifest_units,
    }
    manifest_path = run_dir / 'manifest.json'
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    manifest_path.write_text(payload, encoding='utf-8')
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / 'latest_manifest.json').write_text(payload, encoding='utf-8')
    print(f'AI_WORK_UNITS_READY manifest={manifest_path} units={len(units)} images={sum(len(u["images"]) for u in units)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
