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
        'catalogue_complete': {'avito': complete},
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


@pytest.mark.parametrize('condition', ['inspection_partial', 'skipped', 'blocked', 'missing_proof', 'listed'])
def test_enumeration_alone_never_proves_absence(condition):
    r = report()
    if condition == 'inspection_partial':
        r['catalogue_complete']['avito'] = False
    elif condition == 'skipped':
        r['skipped_cards_by_source'] = {'avito': 1}
    elif condition == 'blocked':
        r['blocked_sources'] = ['avito']
    elif condition == 'missing_proof':
        r.pop('catalogue_complete')
    else:
        r['catalogue_listing_urls'] = {'avito': [URL]}
    assert vehicle_dashboard(r)['inactive'] == 0


def test_unknown_uid_and_new_active_url_remain_unbound_and_not_inactive():
    r = report(active=True, uid=None)
    r['automatic_content_checks']['marketing_vehicles'][0]['unique_id'] = None
    r['observed_cards'][0]['url'] = 'https://www.avito.ru/moskva/avtomobili/car_1234567890'
    fleet = vehicle_dashboard(r)
    assert len(fleet['vehicles']) == 2  # Never join by VIN, price, or model.
    assert fleet['inactive'] == 0
    row = next(t for t in fleet['vehicles'] if t.get('marketing_row'))
    assert next(p for p in row['platforms'] if p['source'] == 'avito')['state'] == 'unknown'


def test_evidenced_direct_removal_survives_incomplete_catalogue():
    from app.scraper.base import evidence_manifest_name
    r = report(complete=False)
    r['observed_cards'] = [{'source': 'avito', 'url': URL, 'state': 'removed',
                            'evidence': 'removed.png', 'evidence_manifest': evidence_manifest_name('removed.png')}]
    assert vehicle_dashboard(r)['inactive'] == 1


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


def test_owner_confirmed_display_group_keeps_missing_id_warning_and_evidence():
    r = report(active=True, uid=None)
    r['observed_cards'][0]['evidence'] = 'description.png'
    before = copy.deepcopy(r)
    proof = {'source': 'avito', 'url': URL, 'unique_id': UID, 'basis': 'Owner confirmed this exact vehicle'}
    d = vehicle_dashboard(r, display_bindings=[proof])
    assert len(d['vehicles']) == 1
    tile = d['vehicles'][0]
    assert tile['incomplete'] and tile['platforms'][0]['state'] == 'active'
    assert tile['display_confirmations'] == [proof]
    assert tile['evidence_image'].endswith('/placement-evidence/0')
    assert r == before and r['observed_cards'][0]['unique_id'] is None


@pytest.mark.parametrize('invalid', ['url', 'uid', 'raw_claim', 'ambiguous'])
def test_display_confirmation_never_overrides_conflicts_or_other_urls(invalid):
    r = report(active=True, uid=None)
    proof = {'source': 'avito', 'url': URL, 'unique_id': UID, 'basis': 'Owner confirmation'}
    proofs = [proof]
    if invalid == 'url':
        proof['url'] += '?different=1'
    elif invalid == 'uid':
        proof['unique_id'] = 'OTHER'
    elif invalid == 'raw_claim':
        r['observed_cards'][0]['card']['placement_id_raw'] = 'unreadable ID'
    else:
        proofs.append(dict(proof))
    assert len(vehicle_dashboard(r, display_bindings=proofs)['vehicles']) == 2


def test_vehicle_photo_template_never_uses_evidence_screenshot_as_car_photo():
    from pathlib import Path
    template = Path('src/app/templates/monitoring_dashboard.html').read_text()
    photo = template.split('<div class="vehicle-photo">', 1)[1].split('<div class="vehicle-body">', 1)[0]
    assert 'evidence_image' not in photo
    assert 'Фото не сохранено' in photo
    assert 'href="{{ car.evidence_image }}"' in template


def test_unbound_card_can_use_exact_link_illustration_without_identity_assignment():
    r = report(active=True, uid=None)
    meta = r['automatic_content_checks']['marketing_vehicles'][0]
    index = {'entries': [{'marketing_vin': VIN, 'site_vins': [VIN],
                         'page_url': 'https://a1auto.ru/cars-for-sale/comfort-guard.html',
                         'image_url': 'https://a1auto.ru/upload/car.jpg'}]}
    fleet = vehicle_dashboard(r, photo_index=index)
    unbound = next(t for t in fleet['vehicles'] if t['unique_id'] is None)
    assert len(fleet['vehicles']) == 2
    assert unbound['image'] == index['entries'][0]['image_url']
    assert unbound['vin'] == '' and unbound['incomplete']
    meta['expected_urls']['avito'] = URL + '123'
    fleet = vehicle_dashboard(r, photo_index=index)
    assert not next(t for t in fleet['vehicles'] if t['unique_id'] is None).get('image')


def test_exact_saved_marketing_link_groups_display_only_with_expected_feed_id_and_row():
    r = report(active=True, uid=None)
    r['automatic_content_checks']['cards'] = [{'source': 'avito', 'url': URL,
        'expected_workbook_ids': [UID], 'expected_marketing_rows': [2],
        'findings': [{'code': 'marketing_identity_unresolved', 'title': 'Missing ID'}]}]
    before = copy.deepcopy(r)
    fleet = vehicle_dashboard(r)
    assert len(fleet['vehicles']) == 1
    tile = fleet['vehicles'][0]
    assert tile['display_confirmations'][0]['kind'] == 'saved_marketing_link'
    assert tile['incomplete'] and tile['issues'][0]['code'] == 'marketing_identity_unresolved'
    assert r == before and r['observed_cards'][0]['unique_id'] is None


@pytest.mark.parametrize('change', ['other_link', 'other_row', 'multiple_ids', 'raw_claim', 'duplicate_metadata'])
def test_display_source_link_refuses_ambiguous_or_conflicting_matches(change):
    r = report(active=True, uid=None)
    reviewed = {'source': 'avito', 'url': URL, 'expected_workbook_ids': [UID], 'expected_marketing_rows': [2]}
    content = r['automatic_content_checks']
    content['cards'] = [reviewed]
    if change == 'other_link':
        content['marketing_vehicles'][0]['expected_urls']['avito'] = URL + '123'
    elif change == 'other_row':
        reviewed['expected_marketing_rows'] = [22]
    elif change == 'multiple_ids':
        reviewed['expected_workbook_ids'].append('OTHER')
    elif change == 'raw_claim':
        r['observed_cards'][0]['card']['placement_id_raw'] = 'Conflicting claim'
    else:
        content['marketing_vehicles'].append(copy.deepcopy(content['marketing_vehicles'][0]))
    fleet = vehicle_dashboard(r)
    assert not any(t.get('display_confirmations') for t in fleet['vehicles'])


def test_verified_feed_photo_is_display_only_and_requires_exact_uid_and_url():
    r = report(active=True)
    r['automatic_content_checks']['marketing_vehicles'] = []
    photo = {'source': 'avito', 'url': URL, 'unique_id': UID,
             'image_url': 'https://storage.yandexcloud.net/a1autofeed/car.jpg'}
    before = copy.deepcopy(r)
    tile = vehicle_dashboard(r, display_photos=[photo])['vehicles'][0]
    assert tile['image'] == photo['image_url']
    assert tile['vin'] == '' and r == before
    photo['unique_id'] = 'OTHER'
    assert vehicle_dashboard(r, display_photos=[photo])['vehicles'][0]['image'] is None
