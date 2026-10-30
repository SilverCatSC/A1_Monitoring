import csv
import io

import pytest

from app.service.placement_feed_snapshot import PlacementFeedError, parse_feed_csv

UID = 'MBVC011220262508260009'
VIN = 'W1VVNLTZ4T4788708'
HEADER = ['Id', 'AvitoId', 'VIN', 'Price', 'Year', 'Description']
SECOND = ['Id', 'Year', 'Price', 'AvitoId', 'Description', 'ImageUrls', 'VIN']


def document(first=None, second=None, header=None):
    first = first or [UID, '1234567890', VIN, '1000000', '2026', 'first text']
    second = second or [UID, '2026', '1000000', '1234567890', 'second text', 'https://images.example/car', VIN]
    buf = io.StringIO()
    csv.writer(buf).writerows([HEADER, ['Обязательный'], ['Подробнее о параметре'], first, [],
                              header or SECOND, ['Обязательный'], ['Подробнее о параметре'], second])
    return buf.getvalue().encode()


def test_reordered_blocks_use_own_header_and_preserve_physical_rows():
    other = 'MBVC011220262508260030'
    rows, info = parse_feed_csv(document(second=[other, '2025', '2000000', '2222222222', 'text', 'https://img', VIN]), 'avito-feed-new')
    assert rows[0]['VIN'] == VIN
    assert rows[5]['Id'] == other  # physical row 9, first data row 4
    assert rows[5]['VIN'] == VIN
    assert rows[5]['Year'] == '2025'
    assert rows[5]['Price'] == '2000000'
    assert rows[1:5] == [{}, {}, {}, {}]
    assert [b['header_row'] for b in info['blocks']] == [1, 6]
    assert info['cell_sources']['9']['VIN'] == [{'row': 9, 'column': 7}]


def test_compatible_copies_merge_without_first_or_last_value_preference():
    rows, info = parse_feed_csv(document(first=[UID, '', VIN, '1000000', '2026', 'first text']), 'avito-feed-new')
    assert sum(bool(r) for r in rows) == 1
    assert rows[0]['AvitoId'] == '1234567890'
    assert rows[0]['Description'] == ''  # divergent descriptions are not selected
    merged = info['merged_duplicates'][0]
    assert merged['rows'] == [4, 9]
    assert merged['field_conflicts'] == ['Description']
    assert info['cell_sources']['4']['AvitoId'] == [{'row': 9, 'column': 4}]
    assert info['cell_sources']['4']['VIN'] == [{'row': 4, 'column': 3}, {'row': 9, 'column': 7}]


def test_different_prices_are_unknown_and_explicitly_recorded():
    rows, info = parse_feed_csv(document(second=[UID, '2026', '2000000', '1234567890', 'first text', '', VIN]), 'avito-feed-new')
    assert rows[0]['Price'] == ''
    assert 'Price' in info['merged_duplicates'][0]['field_conflicts']


@pytest.mark.parametrize('vin,platform_id', [('W1VVNLTZ8T4638988', '1234567890'), (VIN, '2222222222')])
def test_contradictory_identity_stays_separate(vin, platform_id):
    rows, info = parse_feed_csv(document(second=[UID, '2026', '1000000', platform_id, 'text', '', vin]), 'avito-feed-new')
    assert sum(bool(r) for r in rows) == 2
    assert info['merged_duplicates'] == []
    assert info['unresolved_duplicates'] == [{'unique_id': UID, 'rows': [4, 9]}]


def test_no_vin_or_platform_anchor_does_not_merge_by_price():
    rows, info = parse_feed_csv(document(first=[UID, '', '', '1000000', '2026', 'same'],
                                       second=[UID, '2026', '1000000', '', 'same', '', '']), 'avito-feed-new')
    assert sum(bool(r) for r in rows) == 2
    assert info['unresolved_duplicates']


def test_equal_platform_anchor_can_fill_missing_workbook_vin():
    rows, info = parse_feed_csv(document(first=[UID, '1234567890', '', '1000000', '2026', 'same']), 'avito-feed-new')
    assert sum(bool(r) for r in rows) == 1
    assert rows[0]['VIN'] == VIN
    assert info['cell_sources']['4']['VIN'] == [{'row': 9, 'column': 7}]


def test_malformed_nonempty_vin_is_not_silently_filled():
    rows, info = parse_feed_csv(document(first=[UID, '1234567890', 'not-a-vin', '1000000', '2026', 'same']), 'avito-feed-new')
    assert sum(bool(r) for r in rows) == 2
    assert info['unresolved_duplicates']


def test_malformed_repeated_header_fails_closed():
    with pytest.raises(PlacementFeedError, match='identity columns'):
        parse_feed_csv(document(header=['Id', 'AvitoId', 'Price']), 'avito-feed-new')


def test_nonempty_data_beyond_current_header_fails_closed():
    with pytest.raises(PlacementFeedError, match='wider than current header'):
        parse_feed_csv(document(second=[UID, '2026', '1000000', '1234567890', 'text', '', VIN, 'extra']), 'avito-feed-new')


def test_blank_csv_padding_does_not_create_phantom_columns():
    rows, _ = parse_feed_csv(document(second=[UID, '2026', '1000000', '1234567890', 'text', '', VIN, '', '']), 'avito-feed-new')
    assert sum(bool(r) for r in rows) == 1


def test_conflicting_autoru_publish_actions_are_not_merged():
    body = f'Title\nunique_id,action,vin\n{UID},show,{VIN}\n{UID},remove,{VIN}\n'.encode()
    rows, info = parse_feed_csv(body, 'autoru-feed-all')
    assert sum(bool(r) for r in rows) == 2
    assert info['unresolved_duplicates']


@pytest.mark.parametrize('second_value,conflict', [('B', True), ('A', False)])
def test_header_case_aliases_share_values_conflicts_and_physical_sources(second_value, conflict):
    buf = io.StringIO()
    csv.writer(buf).writerows([
        ['Id', 'AvitoId', 'VIN', 'heating'], [], [], [UID, '123', VIN, 'A'],
        ['id', 'avitoid', 'vin', 'Heating'], [], [], [UID, '123', VIN, second_value],
    ])
    rows, info = parse_feed_csv(buf.getvalue().encode(), 'avito-feed-new')
    assert sum(bool(row) for row in rows) == 1
    assert rows[0]['Id'] == UID
    assert 'Heating' not in rows[0]
    assert rows[0]['heating'] == ('' if conflict else 'A')
    assert ('heating' in info['merged_duplicates'][0]['field_conflicts']) is conflict
    assert [(loc['row'], loc['column']) for loc in info['cell_sources']['4']['heating']] == [(4, 4), (8, 4)]
    assert info['blocks'][1]['columns'][-1] == 'Heating'
    if conflict:
        assert info['field_values']['4']['heating'] == ['A', 'B']


def test_case_duplicate_columns_in_one_header_fail_closed():
    with pytest.raises(PlacementFeedError, match='duplicated'):
        parse_feed_csv(b'Id,AvitoId,VIN,heating,Heating\n\n\n', 'avito-feed-new')


def test_marketing_refs_follow_each_blocks_real_columns_and_price_conflict_is_reported():
    from app.service.analytical_summary import build_analytical_summary
    from app.service.automatic_content import automatic_content_review
    from app.service.marketing_content import add_marketing_comparison
    from app.service.placement_feed_snapshot import PlacementFeedSnapshot

    rows, info = parse_feed_csv(document(first=[UID, '1234567890', '', '1000000', '2026', 'ID: ' + UID],
                                       second=[UID, '2026', '2000000', '1234567890', 'ID: ' + UID, '', VIN]), 'avito-feed-new')
    main = [''] * 24
    for index, value in {5: 'Актуально', 10: VIN, 11: '1000000', 12: '2026'}.items():
        main[index] = value
    snap = PlacementFeedSnapshot({'avito-feed-new': rows, 'avito-feed-used': []}, {},
                                 {'avito-feed-new': 4, 'avito-feed-used': 4}, [[], main], 'mainhash',
                                 workbook_url='https://docs.google.com/spreadsheets/d/test/edit',
                                 feed_structure={'avito-feed-new': info})
    cards = [{'source': 'avito', 'url': 'https://www.avito.ru/moskva/avtomobili/car_1234567890',
              'state': 'active', 'card': {'placement_id': UID, 'price': 1000000, 'year_attribute': 2026}}]
    review = automatic_content_review({'cycle_id': 'blocks', 'observed_cards': cards})
    add_marketing_comparison(review, cards, snap)
    card = review['cards'][0]
    assert card['marketing_binding']['status'] == 'matched'
    refs = card['marketing_binding']['cells']
    assert any(r['sheet'] == 'avito-feed-new' and r['a1'] == 'G9' and r['value'] == VIN for r in refs)
    assert not any(r['sheet'] == 'avito-feed-new' and r['a1'] == 'C4' and r['value'] == VIN for r in refs)
    assert 'feed_price_conflict' in {f['code'] for f in card['findings']}
    assert 'feed_price_difference' not in {f['code'] for f in card['findings']}
    assert card['reference_values']['price']['feed'] is None
    assert any(r['a1'] == 'D4' and r['value'] == '1000000' for r in refs)
    assert any(r['a1'] == 'C9' and r['value'] == '2000000' for r in refs)
    brief = build_analytical_summary({'cycle_id': 'blocks', 'observed_cards': cards,
                                      'automatic_content_checks': review})
    assert any(i['group'] == 'quality' and i['vehicle'] == 'Структура фида: avito-feed-new' for i in brief['items'])
    assert any(i['group'] == 'price' and any(d.get('code') == 'feed_price_conflict'
                                          for d in i['details'] if isinstance(d, dict)) for i in brief['items'])
