import json
import subprocess
from pathlib import Path

import pytest

from scripts import publish_shared_dashboard as publisher
from scripts.export_shared_dashboard import project_html, scan_time

CID = '230c09ee-b8b6-4c65-93e1-3fb557964c7e'
STAMP = '2026-11-02T07:20:47+00:00'


def enable(root):
    path = root / publisher.CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'enabled': True, 'scope': 'public-vehicle-dashboard',
                                'vehicle_details_approved': True, 'repository': publisher.REPOSITORY}))
    run = root / 'artifacts/host_runs/test'
    run.mkdir(parents=True)
    return run


def stub_export(monkeypatch):
    def export(base, site, *, cycle_id, checks):
        assert base == 'http://127.0.0.1:18000'
        assert cycle_id == CID
        assert checks == ['Incomplete filter']
        site.mkdir()
        return {'cycle_id': CID, 'scan_at_utc': STAMP}
    monkeypatch.setattr(publisher, 'export', export)
    monkeypatch.setattr('scripts.shared_report_history.prepare_history', lambda *a, **k: None)
    monkeypatch.setattr(publisher, 'publication_checks', lambda *args: ['Incomplete filter'])
    monkeypatch.setattr(publisher, 'confirm_deployment', lambda *args: False)


def test_disabled_by_default_does_not_contact_github(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, 'push_snapshot', lambda *args: pytest.fail('must not publish'))
    assert publisher.publish_after_run(tmp_path, CID)['status'] == 'disabled'


def test_approved_run_pins_cycle_and_records_deployment_separately(tmp_path, monkeypatch):
    run = enable(tmp_path)
    stub_export(monkeypatch)
    monkeypatch.setattr(publisher, 'push_snapshot', lambda *args: 'abc123')
    status = publisher.publish_after_run(tmp_path, CID, run)
    assert status['status'] == 'submitted'  # not a claim that Pages finished building
    assert status['cycle_id'] == CID
    assert status['scan_at_utc'] == STAMP
    assert json.loads((run / 'publication_status.json').read_text()) == status


def test_published_requires_anonymous_readback(tmp_path, monkeypatch):
    run = enable(tmp_path)
    stub_export(monkeypatch)
    monkeypatch.setattr(publisher, 'push_snapshot', lambda *args: 'abc123')
    monkeypatch.setattr(publisher, 'confirm_deployment', lambda *args: True)
    assert publisher.publish_after_run(tmp_path, CID, run)['status'] == 'published'


@pytest.mark.parametrize('code', [0, 2])
def test_monitoring_result_triggers_publication_for_completed_and_partial_reports(tmp_path, monkeypatch, code):
    from scripts import monitoring_result
    calls = []
    monkeypatch.setattr(monitoring_result, 'publish_result', lambda *args:
                        {'report_url': 'http://127.0.0.1:18000/report'})
    monkeypatch.setattr(publisher, 'announce_publication', lambda *args: calls.append(args))
    monkeypatch.setattr('sys.argv', ['monitoring_result', '--cycle-id', CID,
                                    '--run-directory', str(tmp_path), '--exit-code', str(code)])
    monitoring_result.main()
    assert calls[0][1:] == (CID, tmp_path)


def test_missing_report_does_not_publish_an_older_dashboard(tmp_path, monkeypatch):
    from scripts import monitoring_result
    monkeypatch.setattr(monitoring_result, 'publish_result', lambda *args:
                        {'report_url': None, 'status_url': 'http://127.0.0.1:18000/status'})
    monkeypatch.setattr(publisher, 'announce_publication', lambda *args: pytest.fail('must not publish'))
    monkeypatch.setattr('sys.argv', ['monitoring_result', '--cycle-id', CID,
                                    '--run-directory', str(tmp_path), '--exit-code', '2'])
    monitoring_result.main()


def test_network_failure_leaves_local_result_unchanged_and_records_failure(tmp_path, monkeypatch, capsys):
    run = enable(tmp_path)
    (run / 'result.json').write_text('{"status":"partial"}')
    stub_export(monkeypatch)
    def fail(*args):
        raise publisher.PublicationError('push unavailable')
    monkeypatch.setattr(publisher, 'push_snapshot', fail)
    status = publisher.announce_publication(tmp_path, CID, run)
    assert status['status'] == 'failed'
    assert (run / 'result.json').read_text() == '{"status":"partial"}'
    assert json.loads((run / 'publication_status.json').read_text())['status'] == 'failed'
    assert 'local_report_unchanged=true' in capsys.readouterr().out


def test_invalid_approval_cannot_publish(tmp_path, monkeypatch):
    run = enable(tmp_path)
    config = tmp_path / publisher.CONFIG
    value = json.loads(config.read_text())
    value['vehicle_details_approved'] = False
    config.write_text(json.dumps(value))
    monkeypatch.setattr(publisher, 'push_snapshot', lambda *args: pytest.fail('must not publish'))
    assert publisher.publish_after_run(tmp_path, CID, run)['status'] == 'failed'


def test_older_report_cannot_replace_newer_publication(tmp_path):
    (tmp_path / 'publication.json').write_text(json.dumps({'cycle_id': CID, 'scan_at_utc': STAMP}))
    with pytest.raises(publisher.PublicationError, match='newer scan'):
        publisher.protect_newer_snapshot(tmp_path, {'cycle_id': CID, 'scan_at_utc': '2026-10-30T00:00:00+00:00'})
    publisher.protect_newer_snapshot(tmp_path, {'cycle_id': CID, 'scan_at_utc': STAMP})


def test_legacy_publication_gets_scan_date_from_html(tmp_path):
    (tmp_path / 'publication.json').write_text(json.dumps({'cycle_id': CID}))
    (tmp_path / 'index.html').write_text(f'<section id="business-review"><time data-monitor-time="{STAMP}"></time></section>')
    publisher.protect_newer_snapshot(tmp_path, {'cycle_id': CID, 'scan_at_utc': STAMP})


def test_git_push_is_normal_and_contains_only_exported_site(tmp_path):
    site = tmp_path / 'export'
    site.mkdir()
    (site / 'index.html').write_text('Reviewed site')
    calls = []
    def fake(arguments, *, cwd=None):
        calls.append(arguments)
        if 'clone' in arguments:
            repo = Path(arguments[-1])
            repo.mkdir()
            (repo / '.git').mkdir()
            (repo / 'old.html').write_text('obsolete')
        if 'push' in arguments:
            assert not (cwd / 'old.html').exists()
            assert (cwd / 'index.html').read_text() == 'Reviewed site'
            assert sorted(p.name for p in cwd.iterdir()) == ['.git', 'index.html']
        return 'commit123'
    assert publisher.push_snapshot(site, {'cycle_id': CID, 'scan_at_utc': STAMP}, run_command=fake) == 'commit123'
    assert not any('--force' in argument for call in calls for argument in call)
    assert calls[-1][-1] == 'HEAD:refs/heads/gh-pages'


def test_command_does_not_echo_credential_helper_errors(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, '', 'SECRET_TOKEN'))
    with pytest.raises(publisher.PublicationError) as caught:
        publisher.command(['git', 'push'])
    assert 'SECRET_TOKEN' not in str(caught.value)


def test_scan_date_and_incomplete_filters_are_visible_without_javascript():
    from test_shared_dashboard_export import fixture_html
    html = fixture_html().replace('<section id="business-review">',
                                 f'<section id="business-review"><time data-monitor-time="{STAMP}"></time>')
    projected, _ = project_html(html, checks=['Поисковые фильтры завершены: 16 из 17.',
                                            'Не завершено: Auto.ru — Range Rover.'])
    assert 'Дата проверки (UTC): 2026-11-02 07:20:47' in projected
    assert '16 из 17' in projected and 'Не завершено: Auto.ru — Range Rover.' in projected
    assert scan_time(html) == STAMP


def test_recovered_search_is_no_longer_reported_as_unfinished(tmp_path):
    from test_recover_monitoring import project

    from app.service.search_followup import save_search_followup
    root, base, batch = project(tmp_path)
    before = publisher.publication_checks(root, base.name)
    assert any('Не завершено:' in line for line in before)
    save_search_followup(base / 'placement_reconciliation.json', batch, root / 'artifacts/evidence')
    after = publisher.publication_checks(root, base.name)
    assert not any('Не завершено:' in line for line in after)


def test_corrupt_report_bundle_is_not_published(tmp_path, monkeypatch):
    run = enable(tmp_path)
    monkeypatch.setattr(publisher, 'export', lambda *args, **kwargs: pytest.fail('must not export'))
    assert publisher.publish_after_run(tmp_path, CID, run)['status'] == 'failed'
