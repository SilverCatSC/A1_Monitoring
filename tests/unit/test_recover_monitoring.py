import hashlib
import json
import sys

from test_search_followup import fixture

from scripts.recover_monitoring import execute_recovery, latest_run, recovery_plan

CID = 'be539954-0799-4961-8057-a636be7b6ad7'


def project(tmp_path):
    root = tmp_path / 'project'
    evidence, base, report, batch = fixture(root / 'artifacts')
    renamed = base.with_name(CID)
    base.rename(renamed)
    base = renamed
    for name in ['placement_reconciliation.json', 'search_collection.json']:
        path = base / name
        data = json.loads(path.read_text())
        data['cycle_id'] = CID
        path.write_text(json.dumps(data))
    (base / 'analytical_summary.json').write_text('{}')
    (base / 'marketing_summary.txt').write_text('fixture')
    names = ['placement_reconciliation.json', 'analytical_summary.json', 'marketing_summary.txt']
    (base / 'report_bundle.ready.json').write_text(json.dumps({'schema_version': 1, 'files': {
        name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names}}))
    directory = root / 'artifacts/host_runs/run'
    directory.mkdir(parents=True)
    status = {'cycle_id': CID, 'started_at_utc': '2026-10-30T08:00:00Z', 'state': 'partial',
              'network_profile': 'local_routed_diagnostic', 'engines': 'auto_ru,avito', 'pages': 5}
    (directory / 'status.json').write_text(json.dumps(status))
    return root, base, batch


def test_one_action_runs_only_missing_search_and_automatically_updates_report(tmp_path, capsys):
    root, base, batch = project(tmp_path)
    plan = recovery_plan(root, latest_run(root))
    assert plan['kind'] == 'search_only'
    assert plan['filter_keys'] == ['avito_sprinter_all']
    assert plan['arguments'][plan['arguments'].index('--engines') + 1] == 'avito'
    assert '--resume-cycle' not in plan['arguments']
    before = (base / 'placement_reconciliation.json').read_bytes()
    # A fixture child replaces live Chrome. Real publication/validation code runs.
    result = root / 'artifacts/host_runs/followup/search_result.json'
    result.parent.mkdir(parents=True)
    result.write_bytes(batch.read_bytes())
    child = root / 'child.py'
    child.write_text(f'print("HOST_RUNNER_SEARCH_RESULT {result}")\n')
    assert execute_recovery(root, runner=[sys.executable, str(child)]) == 0
    assert 'RECOVERY_REPORT_UPDATED' in capsys.readouterr().out
    assert (base / 'search_followup/ready.json').is_file()
    assert (base / 'placement_reconciliation.json').read_bytes() == before
    assert recovery_plan(root, latest_run(root))['kind'] == 'nothing'


def test_missing_report_uses_checked_resume_instead_of_silent_full_scan(tmp_path):
    root, base, _ = project(tmp_path)
    (base / 'placement_reconciliation.json').unlink()
    plan = recovery_plan(root, latest_run(root))
    assert plan['kind'] == 'resume'
    assert plan['arguments'][-2:] == ['--resume-cycle', CID]
    assert '--diagnostic-routing' in plan['arguments']


def test_finished_checks_do_not_repeat_even_when_original_cycle_is_partial(tmp_path):
    root, base, batch = project(tmp_path)
    collection = json.loads((base / 'search_collection.json').read_text())
    collection['results'][0]['result']['complete'] = True
    (base / 'search_collection.json').write_text(json.dumps(collection))
    assert execute_recovery(root) == 0
