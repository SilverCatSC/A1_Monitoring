import copy
import json
from datetime import date

import pytest

from app.service.analytical_summary import (
    build_analytical_summary,
    load_decisions,
    render_marketing_text,
    save_summary,
    summary_for_report,
    validate_decisions,
)
from app.service.vehicle_dashboard import vehicle_dashboard

UID = 'MBVC011220262508260009'
OTHER = 'MBVC011220262508260010'
AUTO = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1000000001-a/'
AVITO = 'https://www.avito.ru/moskva/avtomobili/example_1000000002'


def decision(kind='confirmed_year', **changes):
    return {'id': 'owner-decision', 'unique_id': UID, 'kind': kind, 'source': 'all',
            'value': 2026, 'confirmed_by': 'Owner', 'basis': 'Confirmed from documents by owner',
            'recorded_on': '2026-01-01', 'active': True, **changes}


def policy(*entries):
    return validate_decisions({'schema_version': 1, 'entries': list(entries)}, today=date(2026, 10, 27))


def report(*, uid=UID, state='active', complete=True, year_conflict=True):
    saved = [{'source': source, 'url': url, 'state': state, 'card': {'price': 12000000},
              'search_positions': [{'page': None, 'label': '5+'}]} for source, url in [('auto_ru', AUTO), ('avito', AVITO)]]
    return {'cycle_id': 'cycle', 'created_at_utc': '2026-10-27T07:00:00Z', 'observed_cards': saved,
            'catalogue_enumeration_complete': {'auto_ru': complete, 'avito': complete},
            'catalogue_complete': {'auto_ru': complete, 'avito': complete},
            'automatic_content_checks': {
                'marketing_vehicles': [{'key': 'row:2', 'marketing_row': 2, 'unique_id': UID,
                    'title': 'Synthetic vehicle', 'price': '12000000', 'expected_urls': {'auto_ru': AUTO, 'avito': AVITO}}],
                'cards': [{'source': c['source'], 'url': c['url'], 'title': 'Synthetic advert',
                    'unique_id': uid, 'marketing_binding': {'status': 'matched' if uid else 'id_missing_or_invalid'},
                    'findings': [{'code': 'year_description_conflict', 'detail': 'Card 2026, description 2025',
                                  'title': 'Year conflict'}] if year_conflict else []} for c in saved],
            }}


def test_same_vehicle_year_conflicts_merge_without_losing_platform_links_or_provenance():
    r = report()
    before = copy.deepcopy(r)
    brief = build_analytical_summary(r, policy(decision()))
    item, = brief['items']
    assert item['group'] == 'year' and item['status'] == 'confirmed'
    assert len(item['links']) == 2 and len(item['details']) == 2
    assert '2026' in item['action'] and item['confirmations'][0]['basis']
    assert r == before
    assert 'Confirmed from documents' in render_marketing_text(brief)


def test_avito_only_summary_does_not_raise_unselected_autoru_binding_questions():
    r = report(uid=None, year_conflict=False)
    r['observed_cards'] = [c for c in r['observed_cards'] if c['source'] == 'avito']
    r['catalogue_enumeration_complete'] = {'avito': True}
    r['catalogue_complete'] = {'avito': True}
    content = r['automatic_content_checks']
    content['cards'] = [c for c in content['cards'] if c['source'] == 'avito']
    content['marketing_vehicles'][0]['unique_id'] = None
    before = copy.deepcopy(r)
    brief = build_analytical_summary(r)
    assert all('auto_ru' not in i['sources'] for i in brief['items'])
    assert any(x['source'] == 'auto_ru' and x['title'] == 'Площадка не проверялась'
               for x in brief['limitations'])
    assert not any(x.get('source') == 'auto_ru' and x['title'] != 'Площадка не проверялась'
                   for x in brief['limitations'])
    assert r == before


def test_partner_exception_only_applies_to_exact_uid_and_marketplace():
    r = report(state='removed', year_conflict=False)
    brief = build_analytical_summary(r, policy(decision('placement_exception', source='avito', partner='Partner')))
    assert brief['counts']['exceptions'] == 1
    item, = brief['items']
    assert item['group'] == 'placement' and item['sources'] == ['auto_ru']
    assert brief['exceptions'][0]['source'] == 'avito'
    other = build_analytical_summary(r, policy(decision('placement_exception', unique_id=OTHER,
                                                       source='avito', partner='Partner')))
    assert other['counts']['exceptions'] == 0
    assert set(other['items'][0]['sources']) == {'auto_ru', 'avito'}


@pytest.mark.parametrize('state,complete', [('unknown', True), ('blocked', True), ('removed', False)])
def test_partial_or_unread_card_does_not_produce_marketing_absence(state, complete):
    brief = build_analytical_summary(report(state=state, complete=complete, year_conflict=False))
    assert not any(i['group'] == 'placement' for i in brief['items'])
    assert brief['limitations']


def test_no_year_decision_is_applied_to_advert_without_uid_even_with_same_price():
    brief = build_analytical_summary(report(uid=None), policy(decision()))
    assert brief['counts']['confirmed_cases'] == 0
    assert len([i for i in brief['items'] if i['group'] == 'identity']) == 2
    assert not any(i['confirmations'] for i in brief['items'])


def test_expired_and_revoked_decisions_are_not_applied():
    expired = policy(decision(valid_until='2026-10-26'))
    assert not expired['entries'] and expired['warnings']
    assert not policy(decision(active=False))['entries']
    assert not build_analytical_summary(report(), expired)['counts']['confirmed_cases']


@pytest.mark.parametrize('changes', [
    {'unique_id': 'VIN123'}, {'basis': ''}, {'confirmed_by': ''}, {'value': True},
    {'recorded_on': '2099-01-01'}, {'source': 'elsewhere'},
])
def test_invalid_provenance_or_identity_is_rejected(changes):
    with pytest.raises(ValueError):
        policy(decision(**changes))


def test_conflicts_never_silently_choose_one_confirmed_value():
    with pytest.raises(ValueError):
        policy(decision(), decision(id='second', value=2025))
    with pytest.raises(ValueError):
        policy(decision(source='avito'), decision(id='second', source='auto_ru', value=2025))
    assert policy(decision(active=False, value=2025), decision(id='second'))['entries'][0]['value'] == 2026


def test_broken_registry_does_not_erase_findings(tmp_path):
    (tmp_path / 'monitoring_decisions.json').write_text('{invalid')
    decisions = load_decisions(tmp_path)
    brief = build_analytical_summary(report(), decisions)
    assert decisions['status'] == 'invalid' and decisions['warnings']
    assert brief['counts']['by_group']['year'] == 1
    assert brief['counts']['confirmed_cases'] == 0


def test_registry_symlink_is_rejected(tmp_path):
    outside = tmp_path.parent / 'outside.json'
    outside.write_text(json.dumps({'schema_version': 1, 'entries': [decision()]}))
    (tmp_path / 'monitoring_decisions.json').symlink_to(outside)
    assert load_decisions(tmp_path)['status'] == 'invalid'


def test_ongoing_case_survives_url_change_for_confirmed_uid():
    prior = build_analytical_summary(report())
    r = report()
    r['cycle_id'] = 'new-cycle'
    r['automatic_content_checks']['cards'][0]['url'] = AUTO + '?tracking=changed'
    brief = build_analytical_summary(r, previous=[prior])
    assert brief['items'][0]['status'] == 'ongoing'
    assert brief['items'][0]['first_detected_cycle_id'] == 'cycle'


def test_a_corrected_year_disappears_from_summary_without_changing_observations():
    brief = build_analytical_summary(report(year_conflict=False), policy(decision()))
    assert not brief['items'] and brief['counts']['confirmed_cases'] == 0


def test_unbound_plan_row_only_reports_link_question_not_absent_vehicle():
    r = report(state='removed', year_conflict=False)
    r['automatic_content_checks']['marketing_vehicles'][0]['unique_id'] = None
    brief = build_analytical_summary(r)
    assert not any(i['group'] == 'placement' for i in brief['items'])
    assert any(i['group'] == 'binding' and 'не доказано' in i['action'] for i in brief['items'])


def test_persisted_decision_snapshot_is_not_replaced_on_readback(tmp_path):
    r = report()
    r['analytical_summary'] = build_analytical_summary(r, policy(decision()))
    (tmp_path / 'monitoring_decisions.json').write_text('{broken-now')
    summary = summary_for_report(r, tmp_path, mode='saved_report_readback')
    assert summary['counts']['confirmed_cases'] == 1
    assert summary['decision_snapshot']['status'] == 'available'


def test_sidecars_do_not_overwrite_existing_report_or_summary(tmp_path):
    path = tmp_path / 'placement_reconciliation.json'
    path.write_text(json.dumps(report()))
    before = path.read_bytes()
    brief = build_analytical_summary(report())
    save_summary(brief, tmp_path)
    assert path.read_bytes() == before
    assert json.loads((tmp_path / 'analytical_summary.json').read_text())['cycle_id'] == 'cycle'
    assert 'Synthetic vehicle' in (tmp_path / 'marketing_summary.txt').read_text()
    # An identical committed bundle is safe to replay; different output is refused.
    save_summary(brief, tmp_path)
    with pytest.raises(FileExistsError):
        save_summary({**brief, 'cycle_id': 'changed'}, tmp_path)


def test_visual_alias_and_text_variation_are_not_marketing_errors():
    r = report(year_conflict=False)
    for c in r['automatic_content_checks']['cards']:
        c['findings'] = [{'code': name, 'detail': 'text'} for name in
                         ['identity_visual_alias', 'feed_description_difference']]
    assert not build_analytical_summary(r)['items']


def test_open_question_is_dated_provenance_not_a_new_confirmed_conflict():
    entry = decision('open_question', question='Confirm the correct powertrain', source='auto_ru',
                     basis='Saved review on an earlier date, not refreshed today')
    brief = build_analytical_summary(report(year_conflict=False), policy(entry))
    item, = brief['items']
    assert item['status'] == 'ongoing' and item['group'] == 'quality'
    assert item['question_provenance']['basis'] == entry['basis']
    assert 'не заново подтверждённое' in item['details'][0]


def test_wrong_cycle_or_broken_persisted_decision_cannot_suppress_issues(tmp_path):
    r = report(state='removed', year_conflict=False)
    r['analytical_summary'] = build_analytical_summary(r, policy(
        decision('placement_exception', source='avito', partner='Partner')))
    r['analytical_summary']['cycle_id'] = 'another-cycle'
    summary = summary_for_report(r, tmp_path)
    assert summary['decision_snapshot']['status'] == 'invalid'
    assert summary['counts']['exceptions'] == 0
    assert any('avito' in i['sources'] for i in summary['items'] if i['group'] == 'placement')


def test_partner_annotation_changes_action_badge_but_keeps_observed_absence():
    r = report(state='removed', year_conflict=False)
    r['automatic_content_checks']['marketing_vehicles'][0]['expected_urls']['auto_ru'] = None
    r['analytical_summary'] = build_analytical_summary(r, policy(
        decision('placement_exception', source='avito', partner='Partner')))
    fleet = vehicle_dashboard(r)
    tile, = fleet['vehicles']
    assert tile['status'] == 'exception' and not tile['actionable_inactive']
    assert fleet['inactive'] == 1 and fleet['attention'] == 0
    assert next(p for p in tile['platforms'] if p['source'] == 'avito')['state'] == 'inactive'


def test_partner_exception_cannot_hide_reappearing_active_publication():
    r = report(year_conflict=False)
    r['analytical_summary'] = build_analytical_summary(r, policy(
        decision('placement_exception', source='avito', partner='Partner')))
    fleet = vehicle_dashboard(r)
    assert fleet['vehicles'][0]['status'] == 'ok'
    assert not fleet['vehicles'][0]['placement_exceptions']


def test_owner_confirmed_identity_does_not_hide_missing_published_id():
    r = report(year_conflict=False)
    card = r['automatic_content_checks']['cards'][1]
    card['findings'] = [{'code': 'published_id_missing', 'title': 'Missing published ID',
                         'detail': 'Owner confirmed this identity; current description has no ID.',
                         'action': 'Publish the known ID in the description.'}]
    summary = build_analytical_summary(r)
    item, = summary['items']
    assert item['group'] == 'identity'
    assert item['unique_id'] == UID
    assert item['action'] == 'Publish the known ID in the description.'
    assert 'не подтверждён в прочитанной карточке' not in str(item['details'])
