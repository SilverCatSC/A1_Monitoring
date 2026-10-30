import copy
from dataclasses import replace

import httpx
import pytest

from app.service.automatic_content import automatic_content_review
from app.service.marketing_content import add_marketing_comparison, number
from app.service.placement_feed_snapshot import PlacementFeedSnapshot, read_placement_feed_snapshot

UID = 'MBVC022220212508260023'
VIN = 'W1VVNLTZ8T4638988'
OTHER = 'W1VVNLTZXS4485803'


def snapshot():
    main = [''] * 24
    for col, value in {
        6: 'Актуально',
        10: 'V-VIP',
        11: VIN,
        12: '12 000 000',
        13: '2021',
        14: '50 000 км.',
        15: '2',
        16: 'без НДС',
    }.items():
        main[col - 1] = value
    return PlacementFeedSnapshot(
        {
            'autoru-feed-all': [
                {'unique_id': UID, 'vin': VIN, 'price': '12500000', 'year': '2021', 'run': '50000'}
            ]
        },
        {'autoru-feed-all': 'hash'},
        {'autoru-feed-all': 3},
        [['header'], main],
        'mainhash',
        workbook_url='https://docs.google.com/spreadsheets/d/test/edit',
    )


def compare(s=None, **overrides):
    cards = [
        {
            'source': 'auto_ru',
            'url': 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/',
            'state': 'active',
            'card': {
                'placement_id': UID,
                'vin': OTHER,
                'price': 12000000,
                'year_attribute': 2021,
                'mileage_km': 50000,
                'owners_count': 1,
                'description': 'V-VIP, без НДС',
                **overrides,
            },
        }
    ]
    review = automatic_content_review({'cycle_id': 'test', 'observed_cards': cards})
    add_marketing_comparison(review, cards, s or snapshot())
    return review['cards'][0], review


def test_strict_uid_and_internal_workbook_join_card_vin_ignored_missing_url_allowed():
    c, r = compare()
    assert c['marketing_binding']['status'] == 'matched'
    assert r['summary']['marketing_bindings'] == {'matched': 1}
    assert 'С основным листом совпадает' in c['fields'][0]['verdict']
    assert {f['code'] for f in c['findings']} >= {'feed_price_difference', 'marketing_owners_difference'}
    assert any(ref['a1'] == 'K2' and ref['value'] == VIN for ref in c['marketing_binding']['cells'])
    assert c['marketing_binding']['cells'][0]['a1'] == 'A3'  # fixture header order
    assert c['marketing_binding']['cells'][0]['value'] == UID
    assert c['source_cells'] and len(c['fields']) == 6


def test_no_identity_fallback_from_card_vin_price_url_or_visual_alias():
    for uid in [None, 'МBVC022220212508260023', 'MBVC022220212508260024']:
        c, _ = compare(placement_id=uid, vin=VIN)
        assert c['marketing_binding']['status'] != 'matched'
        assert not any(f['code'] == 'marketing_price_difference' for f in c['findings'])


def test_duplicate_uid_and_multiple_active_rows_fail_closed():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'] *= 2
    assert compare(s)[0]['marketing_binding']['status'] == 'feed_id_duplicate'
    s = snapshot()
    s.marketing_rows.append(copy.deepcopy(s.marketing_rows[1]))
    assert compare(s)[0]['marketing_binding']['status'] == 'marketing_row_duplicate'
    s.marketing_rows[2][5] = 'Архив'
    assert compare(s)[0]['marketing_binding']['status'] == 'matched'


def test_multiple_vins_in_marketing_cell_are_tokens_not_substrings():
    s = snapshot()
    s.marketing_rows[1][10] = VIN + '\n' + OTHER
    assert compare(s)[0]['marketing_binding']['status'] == 'matched'
    s.marketing_rows[1][10] = 'X' + VIN
    assert compare(s)[0]['marketing_binding']['status'] == 'marketing_row_missing'


def test_missing_snapshot_and_fields_never_create_false_matches():
    s = snapshot()
    object.__setattr__(s, 'marketing_sha256', None)
    assert compare(s)[0]['marketing_binding']['status'] == 'snapshot_unavailable'
    s = snapshot()
    s.marketing_rows[1][11] = ''
    c, _ = compare(s)
    assert 'недостаточно данных' in c['fields'][0]['verdict']
    assert 'С основным листом совпадает' not in c['fields'][0]['verdict']


def test_avito_duplicate_id_across_new_and_used_is_unresolved():
    s = snapshot()
    for sheet in ('avito-feed-new', 'avito-feed-used'):
        s.rows_by_sheet[sheet] = [{'Id': UID, 'VIN': VIN}]
        s.first_data_rows[sheet] = 4
    cards = [
        {
            'source': 'avito',
            'url': 'https://www.avito.ru/moskva/avtomobili/v_8176281881',
            'state': 'active',
            'card': {'placement_id': UID},
        }
    ]
    r = automatic_content_review({'cycle_id': 'test', 'observed_cards': cards})
    add_marketing_comparison(r, cards, s)
    assert r['cards'][0]['marketing_binding']['status'] == 'feed_id_duplicate'


def test_number_parser_does_not_confuse_thousands_words_or_missing_with_zero():
    assert number('0 км.') == 0 and number('19\u00a0990\u00a0000 ₽') == 19990000
    assert number('50 тыс. км') is None and number('') is None


def test_reader_main_sheet_failure_does_not_destroy_feed_and_is_explicit():
    def respond(request):
        gid = request.url.params['gid']
        if gid == '0':
            return httpx.Response(503)
        text = (
            'Title,,,\ncar,unique_id,action,vin\ncar,' + UID + ',show,' + VIN + '\n'
            if gid == '779695926'
            else 'Id,AvitoId,VIN\n,,\n,,\n' + UID + ',123,' + VIN
        )
        return httpx.Response(200, text=text, headers={'content-type': 'text/csv'})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        s = read_placement_feed_snapshot('https://docs.google.com/spreadsheets/d/test/edit', client=client)
    assert s.rows_by_sheet['autoru-feed-all'][0]['unique_id'] == UID
    assert s.marketing_rows == [] and s.marketing_sha256 is None and s.marketing_error
    assert s.read_started_at_utc <= s.read_finished_at_utc


def test_differences_do_not_replace_values_and_vat_options_are_not_false_conflict():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['with_nds'] = 'true'
    c, _ = compare(s, price=13000000, year_attribute=2020, mileage_km=60000)
    codes = {f['code'] for f in c['findings']}
    assert {
        'marketing_price_difference',
        'marketing_year_difference',
        'marketing_mileage_difference',
        'feed_vat_difference',
    } <= codes
    assert c['fields'][0]['observed'] == '13000000'
    c, _ = compare(description='Оплата с НДС и без НДС')
    assert 'marketing_vat_difference' not in {f['code'] for f in c['findings']}
    assert 'не подтверждён однозначно' in c['fields'][4]['verdict']


def test_missing_workbook_vin_can_link_feed_platform_id_to_main_x_after_id_is_established():
    s = snapshot()
    s.rows_by_sheet['avito-feed-new'] = [{'Id': UID, 'VIN': '', 'AvitoId': '8176281881'}]
    s.rows_by_sheet['avito-feed-used'] = []
    s.first_data_rows['avito-feed-new'] = 4
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][23] = 'https://www.avito.ru/moskva/avtomobili/v_8176281881'
    cards = [
        {'source': 'avito', 'url': s.marketing_rows[1][23], 'state': 'active', 'card': {'placement_id': UID}}
    ]

    def check():
        r = automatic_content_review({'cycle_id': 'test', 'observed_cards': cards})
        add_marketing_comparison(r, cards, s)
        return r['cards'][0]['marketing_binding']

    assert check()['row_basis'] == 'workbook_avito_id'
    s.marketing_rows[1][23] = 'https://www.avito.ru/moskva/avtomobili/v_9999999999'
    assert check()['status'] == 'marketing_row_missing'
    s.rows_by_sheet['avito-feed-new'][0]['VIN'] = 'not a VIN'
    assert check()['status'] == 'feed_vin_ambiguous'


def test_verified_autoru_id_can_use_declared_url_to_link_vin_empty_workbook_row():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    c, _ = compare(s)
    assert c['marketing_binding']['status'] == 'matched'
    assert c['marketing_binding']['row_basis'] == 'workbook_confirmed_id_reference'
    assert c['unique_id'] == UID
    assert 'marketing_vin_empty' in {f['code'] for f in c['findings']}
    assert compare(s, placement_id=None)[0]['marketing_binding']['status'] == 'id_missing_or_invalid'


def test_autoru_verified_uid_can_follow_sibling_feed_avito_id_inside_workbook():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    s.rows_by_sheet['avito-feed-new'] = [{'Id': UID, 'VIN': VIN, 'AvitoId': '8176281881'}]
    s.first_data_rows['avito-feed-new'] = 4
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][23] = 'https://www.avito.ru/moskva/avtomobili/car_8176281881'
    c, _ = compare(s)
    assert c['marketing_binding']['status'] == 'matched'
    assert c['marketing_binding']['marketing_row'] == 2
    assert any(r['sheet'] == 'avito-feed-new' and r['value'] == '8176281881'
               for r in c['marketing_binding']['cells'])


def test_known_feed_vin_can_link_blank_main_vin_by_independent_publication_reference():
    s = snapshot()
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    assert compare(s)[0]['marketing_binding']['status'] == 'matched'
    # A nonblank conflicting workbook VIN cannot be ignored in favour of a URL.
    s.marketing_rows[1][10] = OTHER
    assert compare(s)[0]['marketing_binding']['status'] == 'marketing_row_missing'


def test_vin_empty_reference_must_not_choose_between_two_active_rows():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    s.marketing_rows.append(copy.deepcopy(s.marketing_rows[1]))
    assert compare(s)[0]['marketing_binding']['status'] == 'marketing_row_duplicate'


def test_reference_fallback_does_not_override_bad_vin_or_global_id_conflict():
    s = snapshot()
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = 'invalid'
    assert compare(s)[0]['marketing_binding']['status'] == 'feed_vin_ambiguous'
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    s.rows_by_sheet['avito-feed-new'] = [{'Id': UID, 'VIN': VIN}, {'Id': UID, 'VIN': OTHER}]
    s.first_data_rows['avito-feed-new'] = 4
    assert compare(s)[0]['marketing_binding']['status'] != 'matched'


def test_missing_ad_id_can_show_workbook_expectation_but_cannot_bind_by_declared_url():
    s = snapshot()
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    c, _ = compare(s, placement_id=None)
    assert c['expected_workbook_ids'] == [UID]
    assert c['expected_marketing_rows'] == [2]
    assert c['expected_id_is_publication_binding'] is False
    assert c['unique_id'] is None
    assert c['marketing_binding']['status'] == 'id_missing_or_invalid'
    assert 'marketing_price_difference' not in {f['code'] for f in c['findings']}


def test_empty_vin_reference_does_not_hide_other_active_nonempty_reference():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    s.rows_by_sheet['avito-feed-new'] = [{'Id': UID, 'VIN': VIN, 'AvitoId': '8176281881'}]
    s.first_data_rows['avito-feed-new'] = 4
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    competing = copy.deepcopy(s.marketing_rows[1])
    competing[10] = VIN
    competing[22] = ''
    competing[23] = 'https://www.avito.ru/moskva/avtomobili/car_8176281881'
    s.marketing_rows.append(competing)
    assert compare(s)[0]['marketing_binding']['status'] == 'marketing_row_duplicate'


def test_empty_vin_reference_does_not_override_sibling_uid_vin_mapping():
    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    s.rows_by_sheet['avito-feed-new'] = [{'Id': UID, 'VIN': VIN, 'AvitoId': ''}]
    s.first_data_rows['avito-feed-new'] = 4
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    competing = copy.deepcopy(s.marketing_rows[1])
    competing[10] = VIN
    competing[22] = ''
    s.marketing_rows.append(competing)
    assert compare(s)[0]['marketing_binding']['status'] == 'marketing_row_duplicate'


@pytest.mark.parametrize('reverse', [False, True])
def test_late_reference_conflict_invalidates_earlier_comparison_and_dashboard_join(reverse):
    from app.service.vehicle_dashboard import vehicle_dashboard

    s = snapshot()
    s.rows_by_sheet['autoru-feed-all'][0]['vin'] = ''
    avito_url = 'https://www.avito.ru/moskva/avtomobili/car_8176281881'
    s.rows_by_sheet['avito-feed-new'] = [{'Id': UID, 'VIN': VIN, 'AvitoId': '8176281881'}]
    s.first_data_rows['avito-feed-new'] = 4
    auto_url = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
    s.marketing_rows[1][10] = ''
    s.marketing_rows[1][22] = auto_url
    competing = copy.deepcopy(s.marketing_rows[1])
    competing[10], competing[22], competing[23] = VIN, '', avito_url
    s.marketing_rows.append(competing)
    cards = [{'source': 'avito', 'url': avito_url, 'state': 'active', 'card': {'placement_id': UID, 'price': 12000000}},
             {'source': 'auto_ru', 'url': auto_url, 'state': 'active', 'card': {'placement_id': UID, 'price': 12000000}}]
    if reverse:
        cards.reverse()
    report = {'cycle_id': 'synthetic', 'observed_cards': cards,
              'catalogue_enumeration_complete': {'auto_ru': True, 'avito': True}}
    review = automatic_content_review(report)
    add_marketing_comparison(review, cards, s)
    assert UID in review['identity_conflicts']
    assert review['summary']['marketing_bindings'].get('matched', 0) == 0
    assert all(c['marketing_binding']['status'] != 'matched' for c in review['cards'])
    assert not any(f['code'].startswith('marketing_price') for c in review['cards'] for f in c['findings'])
    report['automatic_content_checks'] = review
    dashboard = vehicle_dashboard(report)
    active = [v for v in dashboard['vehicles'] if any(p['state'] == 'active' for p in v['platforms'])]
    assert len(active) == 2
    assert all(v.get('identity_conflict') and not v.get('marketing_row') for v in active)
    assert dashboard['inactive'] == 0


def test_normal_content_pipeline_extracts_feed_photos_without_marketing_row():
    s = replace(snapshot(), marketing_rows=[], marketing_sha256=None)
    s.rows_by_sheet['autoru-feed-all'][0]['images'] = 'https://storage.yandexcloud.net/a1autofeed/car/1.jpg'
    card, review = compare(s)
    assert card['unique_id'] == UID
    assert card['marketing_binding']['status'] != 'matched'
    assert review['marketing_vehicles'] == []
    assert len(review['feed_photo_candidates']) == 1
    assert review['feed_photo_candidates'][0]['unique_id'] == UID
