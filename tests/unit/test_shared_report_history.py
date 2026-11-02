import json
from pathlib import Path

from scripts.shared_report_history import prepare_history, preserve_archives

CID = '230c09ee-b8b6-4c65-93e1-3fb557964c7e'
OLD = '02287cac-1a7f-4838-a3c9-e3c85defba7d'


def test_archive_preserved_when_local_evidence_is_removed(tmp_path):
    repo = tmp_path / 'repo'
    site = tmp_path / 'site'
    for p in (repo, site):
        (p / 'history').mkdir(parents=True)
    archived = repo / 'history' / OLD
    archived.mkdir()
    (archived / 'index.html').write_text('old verified report')
    (repo / 'history-index.json').write_text(
        json.dumps([{'cycle_id': OLD, 'date': '2026-10-30T14:19:00+00:00'}])
    )
    (site / 'history-index.json').write_text('[]')
    (site / 'history.html').write_text('<section id="runs"><h2></h2><ul></ul></section>')
    manifest = {'history': {'reports': 0}}
    preserve_archives(repo, site, manifest)
    assert (site / 'history' / OLD / 'index.html').read_text() == 'old verified report'
    assert OLD in (site / 'history.html').read_text() and manifest['history']['reports'] == 1
    assert f'history/{OLD}/index.html' in manifest['files']


def test_current_snapshot_and_history_are_linked_without_internal_urls(tmp_path, monkeypatch):
    root = tmp_path
    site = root / 'site'
    site.mkdir()
    (site / 'static').mkdir()
    (site / 'index.html').write_text(
        '<nav class="workspace-navigation"><a href="history.html#vehicle-abc">History</a></nav><p class="monitor-note"></p><section><p class="monitor-note">KEEP COVERAGE NOTE</p></section>'
    )
    templates = root / 'src/app/templates'
    templates.mkdir(parents=True)
    templates.joinpath('monitoring_history.html').write_text(
        (Path(__file__).resolve().parents[2] / 'src/app/templates/monitoring_history.html').read_text()
    )
    data = {
        'reports': [
            {'cycle_id': CID, 'date': '2026-11-02T07:00:00+00:00', 'counts': {'auto_ru': 1, 'avito': 0}}
        ],
        'rows': [],
        'as_of': '2026-11-02',
        'excluded_records': 0,
    }
    monkeypatch.setattr('scripts.shared_report_history.load_history', lambda *a, **k: data)
    manifest = {'cycle_id': CID, 'scan_at_utc': '2026-11-02T07:00:00+00:00'}
    prepare_history(
        root,
        site,
        manifest,
        'http://127.0.0.1:18000',
        export_fn=lambda *a, **k: None,
        checks_fn=lambda *a: [],
    )
    html = (site / 'history' / CID / 'index.html').read_text()
    assert '../../history.html#vehicle-abc' in html and '../../index.html' in html
    assert 'Архивная проверка' in html
    assert 'KEEP COVERAGE NOTE' in html
    assert CID in (site / 'history.html').read_text()
    assert manifest['history']['reports'] == 1
    assert 'history.html' in manifest['files']
