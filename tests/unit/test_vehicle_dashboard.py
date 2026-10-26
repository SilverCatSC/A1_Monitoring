import copy

import pytest

from app.service.monitoring_digest import monitoring_digest
from app.service.placement_feed_snapshot import PlacementFeedSnapshot
from app.service.vehicle_dashboard import marketing_vehicle_snapshot, vehicle_dashboard

UID = 'MBVC022220202508260022'
VIN = 'X89183511L1GB1051'
URL = 'https://www.avito.ru/moskva/avtomobili/car_8144331948'


def snapshot():
    row = [''] * 30
    for col, value in {5: 'Актуально', 8: 'V-Class', 10: VIN, 11: '10500000', 23: URL}.items():
        row[col] = value
    return PlacementFeedSnapshot(
        rows_by_sheet={'avito-feed-used': [{'Id': UID, 'VIN': VIN, 'Images': 'https://example.org/car.jpg'}]},
        sha256_by_sheet={}, first_data_rows={}, marketing_rows=[[], row], marketing_sha256='hash',
        workbook_url='https://docs.google.com/spreadsheets/d/test/edit',
    )


def report(*, active=False, complete=True, uid=UID, positions=None):
    data = {
        'cycle_id': 'cycle', 'catalogue_enumeration_complete': {'avito': complete},
        'observed_cards': [], 'automatic_content_checks': {'marketing_vehicles': marketing_vehicle_snapshot(snapshot()), 'cards': []},
    }
    if active:
        data['observed_cards'] = [{'source': 'avito', 'url': URL, 'state': 'active', 'unique_id': uid,
                                  'search_positions': positions or [], 'card': {'price': 10500000}}]
    return data


def test_absent_expected_publication_is_signal_only_after_complete_catalogue():
    full = vehicle_dashboard(report())
    assert full['inactive'] == 1
    assert full['vehicles'][0]['attention']
    partial = vehicle_dashboard(report(complete=False))
    assert partial['inactive'] == 0
    assert partial['vehicles'][0]['platforms'][1]['state'] == 'unknown'


def test_active_five_plus_is_not_inactive_and_uid_republication_is_grouped():
    r = report(active=True, positions=[{'label': '5+', 'page': None}])
    r['observed_cards'][0]['url'] = 'https://www.avito.ru/moskva/avtomobili/car_1234567890'
    d = vehicle_dashboard(r)
    assert len(d['vehicles']) == 1
    assert d['inactive'] == 0
    assert d['vehicles'][0]['platforms'][0]['position'] == '5+'


@pytest.mark.parametrize('state', ['unknown', 'blocked', 'error', None])
def test_unverified_direct_card_never_becomes_inactive_with_complete_catalogue(state):
    r = report(active=True)
    r['observed_cards'][0]['state'] = state
    d = vehicle_dashboard(r)
    assert d['inactive'] == 0
    assert d['vehicles'][0]['incomplete']
    platform = next(p for p in d['vehicles'][0]['platforms'] if p['source'] == 'avito')
    assert platform['state'] == 'unknown'


def test_missing_id_never_joined_by_price_or_url_and_active_url_prevents_false_absence():
    d = vehicle_dashboard(report(active=True, uid=None))
    assert len(d['vehicles']) == 2
    assert d['inactive'] == 0
    unbound = next(t for t in d['vehicles'] if t['unique_id'] is None)
    assert unbound['vin'] == ''
    assert unbound['incomplete']


def test_no_link_is_not_expected_and_actual_publication_still_shown():
    r = report()
    r['automatic_content_checks']['marketing_vehicles'][0]['expected_urls']['avito'] = None
    assert vehicle_dashboard(r)['inactive'] == 0
    r['observed_cards'] = report(active=True)['observed_cards']
    assert vehicle_dashboard(r)['vehicles'][0]['platforms'][0]['state'] == 'active'


def test_workbook_duplicate_and_conflicting_uid_do_not_join():
    s = snapshot()
    s.marketing_rows.append(list(s.marketing_rows[1]))
    assert all(v['unique_id'] is None for v in marketing_vehicle_snapshot(s))
    s = snapshot()
    s.rows_by_sheet['avito-feed-used'].append(dict(s.rows_by_sheet['avito-feed-used'][0]))
    assert marketing_vehicle_snapshot(s)[0]['unique_id'] is None


def test_digest_does_not_promote_text_differences_or_unknown_coverage_to_errors():
    r = report()
    r['publication_alerts'] = {'items': [{'kind': 'publication_disappeared', 'certainty': 'coverage_unproven'}]}
    r['automatic_content_checks']['cards'] = [{'findings': [
        {'code': 'feed_description_difference', 'title': 'Разный текст'},
        {'code': 'marketing_year_difference', 'title': 'Год'},
        {'code': 'future_unknown_code', 'title': 'Новый код'},
    ]}]
    before = copy.deepcopy(r)
    d = monitoring_digest(r)
    assert len(d['notes']) == 1 and len(d['content']) == 1 and len(d['limitations']) == 2
    assert not d['placement']
    assert r == before


def test_restoration_and_republication_are_events():
    r = {'publication_alerts': {'items': [{'kind': k, 'certainty': 'confirmed'}
                                       for k in ('republished', 'publication_restored')]}}
    d = monitoring_digest(r)
    assert len(d['events']) == 2 and not d['placement']


def test_already_verified_workbook_binding_is_reused_without_new_vin_inference():
    r = report(active=True)
    meta = r['automatic_content_checks']['marketing_vehicles'][0]
    meta['unique_id'] = None
    r['automatic_content_checks']['cards'] = [{'source': 'avito', 'url': URL, 'unique_id': UID,
        'marketing_binding': {'status': 'matched', 'marketing_row': 2, 'row_basis': 'workbook_avito_id'}}]
    assert len(vehicle_dashboard(r)['vehicles']) == 1


def test_missing_source_or_unsafe_images_are_not_rendered_as_trusted_links():
    r = report()
    r['automatic_content_checks']['marketing_vehicles'][0]['image'] = 'javascript:alert(1)'
    assert vehicle_dashboard(r)['vehicles'][0]['image'] is None
