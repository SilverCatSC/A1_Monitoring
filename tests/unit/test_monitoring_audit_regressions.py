import copy
import json

import pytest
from test_analytical_summary import AUTO, AVITO, UID, decision, policy, report

from app.service.analytical_summary import build_analytical_summary, save_summary
from app.service.artifact_bundle import publish_bundle, verify_bundle
from app.service.automatic_content import automatic_content_review
from app.service.marketing_content import add_marketing_comparison
from app.service.placement_feed_snapshot import PlacementFeedSnapshot
from app.service.vehicle_dashboard import marketing_vehicle_snapshot, vehicle_dashboard
from app.service.workbook_identity import workbook_identity_conflicts


def workbook(*, documented_pair=False, missing_avito_vin=False, reverse=False):
    vin1, vin2 = 'X0000000000000001', 'X0000000000000002'

    def row(vin, name):
        values = [''] * 30
        for col, value in {5: 'Актуально', 8: name, 10: vin, 11: '1000000', 12: '2026',
                           22: AUTO, 23: AVITO}.items():
            values[col] = value
        return values

    rows = [row(vin1 + '\n' + vin2, 'Documented pair')] if documented_pair else [row(vin1, 'First'), row(vin2, 'Second')]
    if missing_avito_vin:
        rows = rows[:1]
    if reverse:
        rows.reverse()
    return PlacementFeedSnapshot(
        rows_by_sheet={
            'autoru-feed-all': [{'unique_id': UID, 'vin': vin1, 'price': '1000000', 'year': '2026'}],
            'avito-feed-new': [{'Id': UID, 'VIN': '' if missing_avito_vin else vin2,
                                'Price': '1000000', 'Year': '2026', 'AvitoId': '1000000002'}],
            'avito-feed-used': [],
        }, sha256_by_sheet={}, first_data_rows={}, marketing_rows=[[], *rows], marketing_sha256='hash',
        workbook_url='https://docs.google.com/spreadsheets/d/synthetic/edit',
    )


def compared(snapshot):
    cards = [{'source': source, 'url': url, 'state': 'active', 'card': {
        'placement_id': UID, 'placement_id_raw': UID, 'year_attribute': 2026, 'price': 1000000,
        'title': f'{source} synthetic 2026', 'description': 'ID: ' + UID + '\nГод выпуска: 2026',
    }} for source, url in [('auto_ru', AUTO), ('avito', AVITO)]]
    r = {'cycle_id': 'regression', 'observed_cards': cards,
         'catalogue_enumeration_complete': {'auto_ru': True, 'avito': True}}
    content = automatic_content_review(r)
    add_marketing_comparison(content, cards, snapshot)
    r['automatic_content_checks'] = content
    return r


@pytest.mark.parametrize('reverse', [False, True])
def test_cross_feed_uid_conflict_never_selects_first_marketing_vehicle(reverse):
    r = compared(workbook(reverse=reverse))
    before = copy.deepcopy(r)
    content = r['automatic_content_checks']
    assert all(c['marketing_binding']['status'] == 'workbook_identity_conflict' for c in content['cards'])
    assert all(v['unique_id'] is None for v in content['marketing_vehicles'])
    fleet = vehicle_dashboard(r)
    active = [v for v in fleet['vehicles'] if any(p['state'] == 'active' for p in v['platforms'])]
    assert len(active) == 2
    assert all(not v.get('marketing_row') and v['identity_conflict'] for v in active)
    assert fleet['inactive'] == 0
    brief = build_analytical_summary(r, policy(decision()))
    assert brief['counts']['confirmed_cases'] == 0
    assert any(i['group'] == 'identity' and 'противоречащими' in i['details'][0] for i in brief['items'])
    assert r == before


def test_legacy_matched_bindings_cannot_bypass_new_conflict_guard():
    r = compared(workbook())
    c = r['automatic_content_checks']
    c.pop('identity_conflicts')
    for n, item in enumerate(c['cards'], 2):
        item['marketing_binding'].update(status='matched', marketing_row=n)
    c['marketing_vehicles'] = [dict(v, unique_id=UID, identity_conflict_ids=[]) for v in c['marketing_vehicles']]
    assert len([v for v in vehicle_dashboard(r)['vehicles'] if any(p['state'] == 'active' for p in v['platforms'])]) == 2


def test_two_documented_vins_in_one_workbook_row_are_not_two_vehicles():
    s = workbook(documented_pair=True)
    assert not workbook_identity_conflicts(s)
    r = compared(s)
    assert len(vehicle_dashboard(r)['vehicles']) == 1


def test_missing_vin_in_one_feed_is_not_a_contradiction():
    s = workbook(missing_avito_vin=True)
    assert not workbook_identity_conflicts(s)
    assert marketing_vehicle_snapshot(s)[0]['unique_id'] == UID


def test_confirmed_year_detects_consistently_wrong_sources_without_any_existing_finding():
    r = report(year_conflict=False)
    for saved in r['observed_cards']:
        saved['card'].update(year=2025, year_attribute=2025, title='Synthetic 2025',
                             description='Год выпуска: 2025')
    r['automatic_content_checks']['marketing_vehicles'][0]['year'] = '2025'
    for c in r['automatic_content_checks']['cards']:
        c['reference_values'] = {'year': {'marketing': 2025, 'feed': 2025}}
    brief = build_analytical_summary(r, policy(decision(value=2026)))
    item, = brief['items']
    assert item['group'] == 'year' and item['status'] == 'confirmed'
    assert len(item['links']) == 2
    assert '2026' in item['action']
    assert all(d['code'] == 'confirmed_year_difference' for d in item['details'])


@pytest.mark.parametrize('origin', ['year_attribute', 'year_title', 'description', 'marketing', 'feed'])
def test_confirmed_year_checks_each_origin_independently(origin):
    r = report(year_conflict=False)
    for saved in r['observed_cards']:
        saved['card'].update(year_attribute=2026, year_title=2026, description='Год выпуска: 2026')
        if origin in {'year_attribute', 'year_title'}:
            saved['card'][origin] = 2025
        if origin == 'description':
            saved['card']['description'] = 'Год выпуска: 2025'
    for c in r['automatic_content_checks']['cards']:
        c['reference_values'] = {'year': {'marketing': 2026, 'feed': 2026}}
        if origin in {'marketing', 'feed'}:
            c['reference_values']['year'][origin] = 2025
    assert build_analytical_summary(r, policy(decision()))['counts']['by_group']['year'] == 1


def test_missing_year_values_are_explicitly_unknown_not_a_match():
    brief = build_analytical_summary(report(year_conflict=False), policy(decision()))
    assert not brief['items']
    assert sum(item['title'] == 'Нет полей для сверки подтверждённого года' for item in brief['limitations']) == 2


@pytest.mark.parametrize('bad_url', [
    'https://example.org/not-an-avito-ad', 'https://www.avito.ru/brands/a1auto/all/avtomobili',
    'https://auto.ru/cars/used/sale/mercedes/v_klasse/1000000001-a/',
])
def test_wrong_host_category_or_platform_url_is_not_inactive(bad_url):
    r = report(state='removed', year_conflict=False)
    r['automatic_content_checks']['marketing_vehicles'][0]['expected_urls'] = {'avito': bad_url}
    fleet = vehicle_dashboard(r)
    assert fleet['inactive'] == 0
    assert next(p for p in fleet['vehicles'][0]['platforms'] if p['source'] == 'avito')['state'] == 'invalid_link'
    brief = build_analytical_summary(r)
    assert brief['counts']['by_group'].get('placement', 0) == 0
    assert any('Исправить ссылку' in i['action'] for i in brief['items'])


def test_partner_exception_never_hides_invalid_link_for_other_marketplace():
    r = report(state='removed', year_conflict=False)
    r['automatic_content_checks']['marketing_vehicles'][0]['expected_urls']['auto_ru'] = 'https://example.org/wrong'
    r['analytical_summary'] = build_analytical_summary(r, policy(
        decision('placement_exception', source='avito', partner='Partner')))
    assert vehicle_dashboard(r)['vehicles'][0]['attention']


def test_existing_foreign_text_prevents_partial_json_creation(tmp_path):
    (tmp_path / 'marketing_summary.txt').write_text('foreign text')
    with pytest.raises(FileExistsError):
        save_summary(build_analytical_summary(report()), tmp_path)
    assert not (tmp_path / 'analytical_summary.json').exists()
    assert (tmp_path / 'marketing_summary.txt').read_text() == 'foreign text'


def test_interrupted_member_publication_rolls_back_new_artifacts(tmp_path, monkeypatch):
    import app.service.artifact_bundle as module
    original = module.os.link

    def fail_on_commit(source, target):
        if target.name == 'analytical_summary.json':
            raise OSError('simulated storage failure')
        original(source, target)

    monkeypatch.setattr(module.os, 'link', fail_on_commit)
    with pytest.raises(OSError):
        save_summary(build_analytical_summary(report()), tmp_path)
    assert not (tmp_path / 'marketing_summary.txt').exists()
    assert not (tmp_path / 'analytical_summary.json').exists()
    assert not (tmp_path / 'analytical_summary.ready.json').exists()


def test_pending_identical_bundle_recovers_after_hard_interruption(tmp_path):
    import hashlib
    members = {'a.json': '{}', 'b.txt': 'text'}
    manifest = {'schema_version': 1, 'files': {n: hashlib.sha256(v.encode()).hexdigest() for n, v in members.items()}}
    (tmp_path / '.ready.json.pending').write_text(json.dumps(manifest, sort_keys=True) + '\n')
    (tmp_path / 'b.txt').write_text('text')
    publish_bundle(tmp_path, members, marker='ready.json', commit_member='a.json')
    assert verify_bundle(tmp_path, 'ready.json', required=members) == manifest
    publish_bundle(tmp_path, members, marker='ready.json', commit_member='a.json')
    assert not (tmp_path / '.ready.json.pending').exists()


def test_changed_committed_member_is_never_accepted_on_retry(tmp_path):
    members = {'a.json': '{}', 'b.txt': 'text'}
    publish_bundle(tmp_path, members, marker='ready.json', commit_member='a.json')
    (tmp_path / 'b.txt').write_text('changed')
    with pytest.raises(ValueError):
        publish_bundle(tmp_path, members, marker='ready.json', commit_member='a.json')


def test_new_cycle_report_is_unreadable_until_bundle_is_complete(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.service.placement_cycle import PlacementCycleService, settings
    from app.service.placement_report import PlacementReportError, read_cycle_placement_report

    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    r = report()
    r.update(schema_version=1, findings=[])
    r['analytical_summary'] = build_analytical_summary(r)
    path = PlacementCycleService(None, cycle_id='cycle')._write_report(r)
    cycle = SimpleNamespace(id='cycle', summary={'placement_reconciliation': {'report_path': path}})
    assert read_cycle_placement_report(cycle, str(tmp_path))['cycle_id'] == 'cycle'
    (tmp_path / 'cycles/cycle/report_bundle.ready.json').unlink()
    with pytest.raises(PlacementReportError, match='incomplete_report_bundle'):
        read_cycle_placement_report(cycle, str(tmp_path))


def test_storage_failure_does_not_publish_main_cycle_report(tmp_path, monkeypatch):
    import app.service.artifact_bundle as module
    from app.service.placement_cycle import PlacementCycleService, settings

    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    original = module.os.link

    def fail_before_ready(source, target):
        if target.name == 'report_bundle.ready.json':
            raise OSError('simulated disk failure')
        original(source, target)

    monkeypatch.setattr(module.os, 'link', fail_before_ready)
    r = report()
    r.update(schema_version=1, findings=[])
    r['analytical_summary'] = build_analytical_summary(r)
    with pytest.raises(OSError):
        PlacementCycleService(None, cycle_id='cycle')._write_report(r)
    assert not (tmp_path / 'cycles/cycle/placement_reconciliation.json').exists()
    assert not (tmp_path / 'cycles/cycle/analytical_summary.json').exists()


def test_concurrent_report_writers_never_publish_a_mixed_bundle(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = '''
import json,sys
from app.service.artifact_bundle import publish_bundle
value=sys.argv[2]
try:
    publish_bundle(sys.argv[1], {'a.json': json.dumps({'writer': value}), 'b.txt': value},
                   marker='ready.json',commit_member='a.json')
except FileExistsError:
    print('collision')
else:
    print('committed')
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}
    workers = [subprocess.Popen([sys.executable, '-c', script, str(tmp_path), value],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
               for value in ('writerA', 'writerB')]
    outcomes = []
    try:
        for worker in workers:
            out, err = worker.communicate(timeout=20)
            assert worker.returncode == 0, err
            outcomes.append(out.strip())
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    assert sorted(outcomes) == ['collision', 'committed']
    verify_bundle(tmp_path, 'ready.json', required=('a.json', 'b.txt'))
    assert json.loads((tmp_path / 'a.json').read_text())['writer'] == (tmp_path / 'b.txt').read_text()
