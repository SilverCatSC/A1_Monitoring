"""Expose a verified run's report without scanning, authentication changes or source edits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from app.service.artifact_bundle import verify_bundle
from scripts.local_api import env_values


def publish_result(root: Path, run_directory: Path, cycle_id: str, exit_code: int):
    identity = str(UUID(cycle_id))
    if identity != cycle_id or exit_code not in {0, 2}:
        raise ValueError('not a completed report-bearing run')
    directory = root / 'artifacts/evidence/cycles' / identity
    run_directory = run_directory.resolve()
    run_root = (root / 'artifacts/host_runs').resolve()
    if run_directory.parent != run_root or not run_directory.is_dir():
        raise ValueError('result must be saved in this project host_runs directory')
    port = int(env_values(root).get('APP_BIND_PORT', '8000'))
    if not 1 <= port <= 65535:
        raise ValueError('invalid local application port')
    if exit_code == 2 and not (directory / 'placement_reconciliation.json').exists():
        pending = json.loads((directory / 'pending_checks.json').read_text())
        if pending.get('cycle_id') != identity or pending.get('schema_version') != 1:
            raise ValueError('pending checks belong to another cycle')
        result = {'cycle_id': identity, 'status': 'partial', 'report_available': False,
                  'report_url': None, 'reason': 'report_not_generated',
                  'status_url': f'http://127.0.0.1:{port}/api/v1/status/cycles/{identity}',
                  'pending_checks_path': str(directory / 'pending_checks.json'),
                  'evidence_bundle_verified': False}
        (run_directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        return result
    verify_bundle(directory, 'report_bundle.ready.json', required=(
        'placement_reconciliation.json', 'analytical_summary.json', 'marketing_summary.txt'))
    report = json.loads((directory / 'placement_reconciliation.json').read_text())
    if report.get('cycle_id') != identity:
        raise ValueError('report belongs to another cycle')
    url = (f'http://127.0.0.1:{port}/api/v1/dashboard/placement-identity'
           f'?cycle_id={identity}#marketing-summary')
    result = {'cycle_id': identity, 'status': 'completed' if exit_code == 0 else 'partial',
              'report_url': url, 'summary_path': str(directory / 'marketing_summary.txt'),
              'evidence_bundle_verified': True}
    (run_directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--run-directory', required=True)
    parser.add_argument('--exit-code', type=int, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = publish_result(root, Path(args.run_directory), args.cycle_id, args.exit_code)
    if result.get('report_url'):
        print('HOST_RUNNER_REPORT_URL ' + result['report_url'])
    else:
        print('HOST_RUNNER_REPORT_UNAVAILABLE report_not_generated')
        print('HOST_RUNNER_STATUS_URL ' + result['status_url'])


if __name__ == '__main__':
    main()
