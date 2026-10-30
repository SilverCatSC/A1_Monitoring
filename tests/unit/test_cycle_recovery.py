import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models import EngineType
from app.scraper.auto_ru import _list_url
from app.scraper.base import EVIDENCE_SCHEMA_VERSION, ScanResult, evidence_manifest_name
from app.service.cycle_recovery import RecoveryError, RecoveryJournal

NOW = datetime.now(UTC)
URL = 'https://auto.ru/moskva/cars/mercedes/v_klasse/'
CARD = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1234567890-abcd/'
SCOPE = ['auto_ru', 'filter-1', URL, 3, 1]


def evidence(root, name, url, purpose, stamp=NOW, final_url=None):
    image = root / (name + '.png')
    image.write_bytes(b'fixture image bytes')
    payload = {
        'schema_version': EVIDENCE_SCHEMA_VERSION, 'source': 'auto_ru', 'purpose': purpose,
        'page_number': 1, 'screenshot_file': image.name, 'screenshot_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
        'screenshot_bytes': image.stat().st_size, 'captured_at': stamp.isoformat(),
        'requested_url_sha256': hashlib.sha256(url.encode()).hexdigest(),
        'final_url_sha256': hashlib.sha256((final_url or url).encode()).hexdigest(), 'http_status': 200,
    }
    (root / evidence_manifest_name(image.name)).write_text(json.dumps(payload))
    return image.name


def search(root, *, complete=True, name='search'):
    requested = _list_url(URL)
    image = evidence(root, name, URL, 'search_page', final_url=requested)
    return ScanResult('filter-1', 1, [], {'page_1_evidence': image, 'page_1_requested_url': requested,
                                      'page_1_final_url': requested},
                      NOW, requested_pages=1, complete=complete)


def card(root):
    image = evidence(root, 'card', CARD, 'direct_card')
    description = 'Full description ID: MBVC011220262508260009'
    return {'state': 'active', 'evidence': image, 'card': {
        'price': 1000000, 'description': description, 'description_capture': {'software_truncated': False,
            'sha256': hashlib.sha256(description.encode()).hexdigest()},
    }}


def journal(root, name, parent=None, config='same', clock=lambda: NOW):
    return RecoveryJournal(root, name, parent_id=parent, config=config, now=clock)


def forbidden():
    raise AssertionError('completed work must not run again')


def test_offline_recovery_selects_only_platforms_with_saved_work():
    from scripts.check_recovery_saved import replay_sources
    avito_card = {'source': 'avito', 'state': 'active'}
    entry = {'source': 'avito', 'result': {'complete': True}}
    pairs, missing = replay_sources({'observed_cards': [avito_card],
                                    'catalogue_enumeration_complete': {'avito': True}},
                                   {'results': [entry]})
    assert pairs == [('avito', entry, avito_card)] and missing == []
    _, missing = replay_sources({'observed_cards': [avito_card],
                                'catalogue_enumeration_complete': {'avito': True, 'auto_ru': False}},
                               {'results': [entry]})
    assert missing == ['auto_ru']
    with pytest.raises(ValueError, match='no completed'):
        replay_sources({'observed_cards': []}, {'results': [entry]})


def test_completed_search_survives_interruption_without_repeating_it(tmp_path):
    first = journal(tmp_path, 'first')
    result = first.search(SCOPE, lambda: search(tmp_path))
    # A new process/attempt has no access to the old in-memory result.
    second = journal(tmp_path, 'second', 'first')
    recovered = second.search(SCOPE, forbidden)
    assert recovered.scanned_at == result.scanned_at
    assert recovered.diagnostics['recovery']['source_cycle_id'] == 'first'
    assert not recovered.diagnostics['recovery']['fresh_browser_visit']
    assert second.summary()['reused_search_filters'] == 1
    # Recovery can itself be interrupted without losing inherited checkpoints.
    third = journal(tmp_path, 'third', 'second')
    assert third.search(SCOPE, forbidden).scanned_at == NOW


def test_unfinished_search_is_not_saved_as_success(tmp_path):
    first = journal(tmp_path, 'first')
    assert not first.search(SCOPE, lambda: search(tmp_path, complete=False)).complete
    second = journal(tmp_path, 'second', 'first')
    calls = []
    result = second.search(SCOPE, lambda: calls.append('retry') or search(tmp_path))
    assert result.complete and calls == ['retry']
    assert second.summary()['reused_search_filters'] == 0


@pytest.mark.parametrize('field,value', [(1, 'other-filter'), (2, URL + '?sort=price'), (3, 4), (4, 2)])
def test_changed_filter_scope_requires_a_real_check(tmp_path, field, value):
    journal(tmp_path, 'first').search(SCOPE, lambda: search(tmp_path))
    second = journal(tmp_path, 'second', 'first')
    scope = list(SCOPE)
    scope[field] = value
    calls = []
    second.search(scope, lambda: calls.append('new') or search(tmp_path, complete=False))
    assert calls == ['new']


def test_settings_and_expired_parent_fail_before_any_scan(tmp_path):
    journal(tmp_path, 'first')
    with pytest.raises(RecoveryError, match='settings'):
        journal(tmp_path, 'second', 'first', config='changed')
    with pytest.raises(RecoveryError, match='expired'):
        journal(tmp_path, 'later', 'first', clock=lambda: NOW + timedelta(hours=7))
    assert not (tmp_path / 'cycles/second').exists()
    assert not (tmp_path / 'cycles/later').exists()


def test_damaged_parent_payload_is_not_reused_or_overwritten(tmp_path):
    first = journal(tmp_path, 'first')
    first.search(SCOPE, lambda: search(tmp_path))
    member = next(p for p in (tmp_path / 'cycles/first/recovery').glob('*/record.json')
                  if p.parent.name != 'manifest')
    member.write_text('{"damaged":true}')
    second = journal(tmp_path, 'second', 'first')
    calls = []
    second.search(SCOPE, lambda: calls.append('fresh') or search(tmp_path, name='fresh'))
    assert calls == ['fresh']
    assert member.read_text() == '{"damaged":true}'


def test_missing_or_changed_image_cannot_be_reused(tmp_path):
    journal(tmp_path, 'first').search(SCOPE, lambda: search(tmp_path))
    (tmp_path / 'search.png').write_bytes(b'changed')
    second = journal(tmp_path, 'second', 'first')
    calls = []
    second.search(SCOPE, lambda: calls.append('fresh') or search(tmp_path, name='fresh'))
    assert calls == ['fresh']


def test_complete_card_retains_original_capture_and_full_description(tmp_path):
    first = journal(tmp_path, 'first')
    original = first.card(EngineType.AUTO_RU, CARD, lambda: card(tmp_path))
    second = journal(tmp_path, 'second', 'first')
    result = second.card(EngineType.AUTO_RU, CARD, forbidden)
    assert result['card'] == original['card']
    assert result['recovery']['captured_at'] == NOW.isoformat()
    assert result['recovery']['source_cycle_id'] == 'first'


@pytest.mark.parametrize('state', ['blocked', 'unknown'])
def test_unverified_card_remains_pending_not_cached(tmp_path, state):
    journal(tmp_path, 'first').card(EngineType.AUTO_RU, CARD, lambda: {'state': state})
    second = journal(tmp_path, 'second', 'first')
    calls = []
    result = second.card(EngineType.AUTO_RU, CARD, lambda: calls.append('fresh') or {'state': 'unknown'})
    assert calls == ['fresh'] and result['state'] == 'unknown'


def test_bad_description_and_wrong_url_are_not_reusable(tmp_path):
    first = journal(tmp_path, 'first')
    value = card(tmp_path)
    value['card']['description_capture']['software_truncated'] = True
    first.card(EngineType.AUTO_RU, CARD, lambda: value)
    calls = []
    second = journal(tmp_path, 'second', 'first')
    second.card(EngineType.AUTO_RU, CARD, lambda: calls.append('fresh') or {'state': 'unknown'})
    assert calls == ['fresh']
    first.card(EngineType.AUTO_RU, CARD + '?other=1', lambda: card(tmp_path))
    assert first.summary()['reused_cards'] == 0


def test_price_change_in_fresh_catalogue_invalidates_saved_card(tmp_path):
    journal(tmp_path, 'first').card(EngineType.AUTO_RU, CARD, lambda: card(tmp_path))
    second = journal(tmp_path, 'second', 'first')
    calls = []
    result = second.card(EngineType.AUTO_RU, CARD,
                         lambda: calls.append('fresh') or {'state': 'unknown'}, price_hint=2000000)
    assert calls == ['fresh'] and result['state'] == 'unknown'


def test_wrong_search_page_cannot_be_saved_as_matching_filter(tmp_path):
    first = journal(tmp_path, 'first')
    value = search(tmp_path)
    value.diagnostics['page_1_requested_url'] = URL + '?other=1'
    first.search(SCOPE, lambda: value)
    second = journal(tmp_path, 'second', 'first')
    calls = []
    second.search(SCOPE, lambda: calls.append('fresh') or search(tmp_path))
    assert calls == ['fresh']


def test_path_traversal_and_symlink_parent_are_rejected(tmp_path):
    with pytest.raises(RecoveryError):
        journal(tmp_path, '../outside')
    (tmp_path / 'cycles').mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'cycles/first').symlink_to(outside)
    with pytest.raises(RecoveryError, match='symlinks'):
        journal(tmp_path, 'first')


def test_checkpoint_is_durable_after_process_exits_without_cleanup(tmp_path):
    # Real process boundary, not an in-memory restart simulation.
    result = search(tmp_path)
    script = '''
import json, os, sys
from datetime import datetime
from app.scraper.base import ScanResult
from app.service.cycle_recovery import RecoveryJournal
root, stamp, scope, diagnostics = sys.argv[1:]
when = datetime.fromisoformat(stamp)
j = RecoveryJournal(root, 'killed', config='same', now=lambda: when)
j.search(json.loads(scope), lambda: ScanResult('filter-1', 1, [], json.loads(diagnostics), when, requested_pages=1))
os._exit(17)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}
    completed = subprocess.run([sys.executable, '-c', script, str(tmp_path), NOW.isoformat(),
                                json.dumps(SCOPE), json.dumps(result.diagnostics)], env=env, check=False)
    assert completed.returncode == 17
    recovered = journal(tmp_path, 'restarted', 'killed')
    assert recovered.search(SCOPE, forbidden).scanned_at == NOW


def test_summary_explicitly_labels_recovered_observations():
    from app.service.analytical_summary import build_analytical_summary, render_marketing_text

    report = {'cycle_id': 'recovered', 'observed_cards': [], 'recovery': {
        'resume_of_cycle_id': 'interrupted', 'reused_search_filters': 2, 'reused_cards': 3,
    }}
    brief = build_analytical_summary(report)
    assert any(item['title'] == 'Продолжение прерванной проверки' for item in brief['limitations'])
    assert 'не новый полный обход' in render_marketing_text(brief)


def test_interrupted_checkpoint_publication_without_marker_is_not_accepted(tmp_path, monkeypatch):
    first = journal(tmp_path, 'first')

    def torn_write(key, payload):
        directory = first._directory(first.cycle_id, key)
        directory.mkdir(parents=True)
        (directory / 'record.json').write_text(json.dumps(payload))
        raise KeyboardInterrupt

    monkeypatch.setattr(first, '_write', torn_write)
    with pytest.raises(KeyboardInterrupt):
        first.search(SCOPE, lambda: search(tmp_path))
    recovered = journal(tmp_path, 'recovered', 'first')
    calls = []
    recovered.search(SCOPE, lambda: calls.append('fresh') or search(tmp_path, name='fresh'))
    assert calls == ['fresh']
    assert recovered.summary()['reused_search_filters'] == 0
