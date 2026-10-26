"""Offline replay must preserve originals and cannot upgrade historical coverage."""
import hashlib
import json
import socket

import pytest
from test_saved_card_evidence_scope import captured

from scripts.check_saved_monitoring import check_saved_report, main, render_saved_check


def inputs(tmp_path):
    saved, manifest = captured(tmp_path)
    (tmp_path / 'card.png.json').write_text(json.dumps(manifest))
    # Long text with identity at the tail exercises the full saved-text path.
    text = 'Индивидуальное описание автомобиля. ' * 160 + '\nID: ' + saved['card']['placement_id']
    saved['card']['description'] = text
    saved['card']['description_capture']['sha256'] = hashlib.sha256(text.encode()).hexdigest()
    path = tmp_path / 'original.json'
    path.write_text(json.dumps({
        'schema_version': 1, 'cycle_id': 'saved-cycle', 'created_at_utc': '2026-10-01T12:00:00Z',
        'findings': [], 'observed_cards': [saved],
        'catalogue_enumeration_complete': {'auto_ru': True, 'avito': False},
        'catalogue_complete': {'auto_ru': False, 'avito': False},
    }))
    return path


def test_replay_runs_without_network_retains_long_description_and_separate_coverage(tmp_path, monkeypatch):
    path = inputs(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}

    def forbidden(*args, **kwargs):
        raise AssertionError('offline replay tried to access network')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    result = check_saved_report(path, tmp_path)
    assert result['integrity_status'] == 'passed'
    assert result['historical_report_created_at_utc'] == '2026-10-01T12:00:00Z'
    assert result['live_acceptance'] == 'not_run' and result['new_live_scan'] is False
    assert result['catalogue_enumeration_complete']['auto_ru'] is True
    assert result['catalogue_direct_checks_complete']['auto_ru'] is False
    assert result['source_inputs_unchanged']
    assert result['content_review']['cards'][0]['unique_id'] == 'MBVC022220212508260023'
    assert all(p.read_bytes() == data for p, data in before.items())


def test_broken_capture_is_attention_not_full_report_success(tmp_path):
    path = inputs(tmp_path)
    (tmp_path / 'card.png').write_bytes(b'changed')
    result = check_saved_report(path, tmp_path)
    assert result['integrity_status'] == 'needs_attention'
    assert result['integrity_problems'][0]['code'] == 'card_evidence_unverified'


def test_unrelated_search_cannot_replace_cycle_positions(tmp_path):
    path = inputs(tmp_path)
    search = tmp_path / 'search.json'
    search.write_text(json.dumps({'cycle_id': 'different-cycle', 'results': []}))
    with pytest.raises(ValueError, match='another cycle'):
        check_saved_report(path, tmp_path, search_path=search)


def test_output_cannot_overwrite_source_even_with_html_suffix(tmp_path, monkeypatch):
    path = inputs(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr('sys.argv', ['check_saved_monitoring', '--report', str(path),
                                   '--evidence-dir', str(tmp_path), '--output', str(path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and path.read_bytes() == before


def test_report_escapes_saved_urls_and_explains_offline_boundary(tmp_path):
    path = inputs(tmp_path)
    result = check_saved_report(path, tmp_path)
    result['integrity_problems'] = [{'code': 'test', 'url': '<script>bad</script>'}]
    markup = render_saved_check(result)
    assert '<script>bad</script>' not in markup and '&lt;script&gt;' in markup
    assert 'Живой прогон не выполнялся' in markup


def test_short_search_requires_proven_exhaustion_not_merely_complete_flag(tmp_path):
    path = inputs(tmp_path)
    search = tmp_path / 'search.json'
    data = {'cycle_id': 'saved-cycle', 'results': [{
        'filter_id': 'filter', 'source': 'auto_ru', 'url': 'https://auto.ru/moskva/cars/hongqi/hq9/new/',
        'pages_limit': 5, 'result': {'page_count': 1, 'complete': True, 'exhausted': False, 'hits': []},
    }]}
    search.write_text(json.dumps(data))
    result = check_saved_report(path, tmp_path, search_path=search)
    assert result['integrity_status'] == 'needs_attention'
    assert result['integrity_problems'][0]['code'] == 'search_completion_unproven'
    data['results'][0]['result']['exhausted'] = True
    search.write_text(json.dumps(data))
    assert check_saved_report(path, tmp_path, search_path=search)['integrity_status'] == 'passed'
