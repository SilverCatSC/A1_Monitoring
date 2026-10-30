import json
import subprocess
from pathlib import Path

import pytest

from app.service.artifact_bundle import publish_bundle
from scripts.monitoring_result import publish_result

CID = '10d83fb3-ffdd-4aa7-afac-7dbf8e03a99e'


def fixture(tmp_path, *, report_id=CID):
    directory = tmp_path / 'artifacts/evidence/cycles' / CID
    publish_bundle(directory, {
        'placement_reconciliation.json': json.dumps({'cycle_id': report_id}),
        'analytical_summary.json': '{}', 'marketing_summary.txt': 'Saved issues',
    }, marker='report_bundle.ready.json', commit_member='placement_reconciliation.json')
    run = tmp_path / 'artifacts/host_runs/run'
    run.mkdir(parents=True)
    (tmp_path / '.env').write_text('APP_BIND_PORT=18000\nADMIN_PASSWORD=do-not-publish\n')
    return directory, run


@pytest.mark.parametrize('code,status', [(0, 'completed'), (2, 'partial')])
def test_report_result_uses_verified_bundle_and_preserves_source(tmp_path, code, status):
    directory, run = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    result = publish_result(tmp_path, run, CID, code)
    assert result['status'] == status
    assert result['report_url'].startswith('http://127.0.0.1:18000/')
    assert CID in result['report_url']
    assert 'do-not-publish' not in (run / 'result.json').read_text()
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


def test_report_result_rejects_corrupt_bundle(tmp_path):
    directory, run = fixture(tmp_path)
    (directory / 'marketing_summary.txt').write_text('changed')
    with pytest.raises(ValueError):
        publish_result(tmp_path, run, CID, 0)
    assert not (run / 'result.json').exists()


def test_report_result_rejects_foreign_cycle_or_directory(tmp_path):
    _, run = fixture(tmp_path, report_id='other')
    with pytest.raises(ValueError, match='another cycle'):
        publish_result(tmp_path, run, CID, 0)


def test_report_result_never_presents_failure_as_completed(tmp_path):
    _, run = fixture(tmp_path)
    with pytest.raises(ValueError):
        publish_result(tmp_path, run, CID, 1)
    assert not (run / 'result.json').exists()


def test_partial_without_report_produces_explicit_unavailable_status(tmp_path):
    directory = tmp_path / 'artifacts/evidence/cycles' / CID
    directory.mkdir(parents=True)
    (directory / 'pending_checks.json').write_text(json.dumps({'schema_version': 1, 'cycle_id': CID}))
    run = tmp_path / 'artifacts/host_runs/run'
    run.mkdir(parents=True)
    result = publish_result(tmp_path, run, CID, 2)
    assert result['report_url'] is None and result['report_available'] is False
    assert not result['evidence_bundle_verified']
    assert CID in result['status_url']


def test_manual_start_runs_the_same_controlled_host_path():
    text = (Path(__file__).resolve().parents[2] / 'Start Monitoring.command').read_text()
    assert '--diagnostic-routing --engines auto_ru,avito --pages 5' in text
    assert '--captcha-wait-seconds -1' in text
    assert 'run_monitoring_host_macos.sh' in text


def test_manual_start_preflight_does_not_pass_scan_parameters(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / 'Start Monitoring.command'
    (tmp_path / launcher.name).write_text(launcher.read_text())
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    runner = scripts / 'run_monitoring_host_macos.sh'
    runner.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    runner.chmod(0o700)
    result = subprocess.run(['bash', str(tmp_path / launcher.name), '--preflight'],
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == '--preflight'
