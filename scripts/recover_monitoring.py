"""One manual recovery action: plan unfinished work, run it, publish its report."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from app.service.artifact_bundle import verify_bundle
from app.service.filters import normalize_search_url
from app.service.search_followup import apply_search_followup, save_search_followup
from app.service.targeted_search import TARGETED_FILTER_KEYS, targeted_filter


def latest_run(root):
    candidates = []
    for path in (root / 'artifacts/host_runs').glob('*/status.json'):
        try:
            value = json.loads(path.read_text())
            if value.get('cycle_id'):
                UUID(value['cycle_id'])
                candidates.append((value.get('started_at_utc', ''), value))
        except (OSError, ValueError, TypeError):
            continue
    if not candidates:
        raise ValueError('No saved monitoring cycle found. Open Start Monitoring.command first.')
    return max(candidates, key=lambda item: item[0])[1]


def recovery_plan(root, run):
    identity = str(UUID(run['cycle_id']))
    base = root / 'artifacts/evidence/cycles' / identity
    path = base / 'placement_reconciliation.json'
    mode = run.get('network_profile')
    network = {'local_routed_diagnostic': '--diagnostic-routing',
               'local_no_vpn': '--diagnostic-no-vpn'}.get(mode)
    common = ([network] if network else []) + ['--engines', run.get('engines', 'auto_ru,avito'),
               '--pages', str(run.get('pages', 5)), '--captcha-wait-seconds', '-1', '--pace', run.get('pace', 'normal')]
    if not path.exists():
        return {'kind': 'resume', 'cycle_id': identity, 'arguments': common + ['--resume-cycle', identity]}
    verify_bundle(base, 'report_bundle.ready.json', required=(
        'placement_reconciliation.json', 'analytical_summary.json', 'marketing_summary.txt'))
    report = json.loads(path.read_text())
    if report.get('cycle_id') != identity:
        raise ValueError('Saved report does not match the selected cycle')
    apply_search_followup(report, base.parents[1])
    resolved = set()
    if report.get('search_followup'):
        payload = json.loads((base / 'search_followup/followup.json').read_text())
        resolved = {normalize_search_url(r['url']) for r in payload['records']}
    collection = json.loads((base / 'search_collection.json').read_text())
    missing = [e for e in collection['results'] if not e['result']['complete']
               and normalize_search_url(e['url']) not in resolved]
    incomplete_cards = (report.get('blocked_sources') or any(report.get('skipped_cards_by_source', {}).values())
                        or any(v is not True for v in report.get('catalogue_enumeration_complete', {}).values()))
    if incomplete_cards or any(e['source'] != 'avito' for e in missing):
        return {'kind': 'resume', 'cycle_id': identity, 'arguments': common + ['--resume-cycle', identity]}
    keys = []
    for entry in missing:
        matching = [key for key in TARGETED_FILTER_KEYS if normalize_search_url(targeted_filter(key).url)
                    == normalize_search_url(entry['url'])]
        if len(matching) != 1:
            raise ValueError('An unfinished filter no longer matches the configured scope')
        keys.append(matching[0])
    if not keys:
        return {'kind': 'nothing', 'cycle_id': identity, 'arguments': []}
    return {'kind': 'search_only', 'cycle_id': identity, 'filter_keys': keys,
            'arguments': ([network] if network else []) + ['--engines', 'avito', '--search-only', ','.join(keys),
                          '--pages', str(run.get('pages', 5)), '--captcha-wait-seconds', '-1',
                          '--pace', run.get('pace', 'normal')]}


def execute_recovery(root, *, runner=None):
    root = Path(root).resolve()
    plan = recovery_plan(root, latest_run(root))
    if plan['kind'] == 'nothing':
        print('RECOVERY_NOT_NEEDED All technical checks in this report are already finished. Data findings remain in the report.')
        return 0
    print('RECOVERY_PLAN ' + json.dumps(plan), flush=True)
    command = runner or [str(root / 'scripts/run_monitoring_host_macos.sh')]
    lines = []
    with subprocess.Popen([*command, *plan['arguments']], cwd=root, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            lines.append(line.strip())
        code = process.wait()
    if code != 0 or plan['kind'] != 'search_only':
        return code
    results = [line.removeprefix('HOST_RUNNER_SEARCH_RESULT ') for line in lines
               if line.startswith('HOST_RUNNER_SEARCH_RESULT ')]
    if len(results) != 1:
        raise ValueError('Recovery output does not identify its search evidence')
    batch = Path(results[0]).resolve()
    batch.relative_to(root / 'artifacts/host_runs')
    saved = json.loads(batch.read_text())
    if saved.get('kind') == 'targeted_search':
        batch = batch.with_name('recovery_batch.json')
        with batch.open('x') as stream:
            json.dump({'kind': 'targeted_search_batch', 'writes_monitoring_cycle': False,
                       'records': [saved]}, stream)
    base = root / 'artifacts/evidence/cycles' / plan['cycle_id']
    save_search_followup(base / 'placement_reconciliation.json', batch, root / 'artifacts/evidence')
    from app.service.analytical_summary import save_summary, summary_for_report
    report = json.loads((base / 'placement_reconciliation.json').read_text())
    apply_search_followup(report, root / 'artifacts/evidence')
    save_summary(summary_for_report(report, root / 'artifacts/evidence'), batch.parent / 'updated_summary')
    from scripts.local_api import env_values
    port = int(env_values(root).get('APP_BIND_PORT', '8000'))
    print(f'RECOVERY_REPORT_UPDATED http://127.0.0.1:{port}/api/v1/dashboard/placement-identity'
          f'?cycle_id={plan["cycle_id"]}#marketing-summary', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(execute_recovery(Path(__file__).resolve().parents[1]))
    except (OSError, ValueError) as error:
        print(f'RECOVERY_UNAVAILABLE {error}', file=sys.stderr)
        raise SystemExit(1) from error
