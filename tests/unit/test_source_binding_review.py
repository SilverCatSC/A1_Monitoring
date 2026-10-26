from copy import deepcopy

import pytest

from app.service.source_binding_review import validate_workbook_bindings

ID = 'VODR011220262209260033'
VIN = 'LDP95H962PE117895'
URL = 'https://www.avito.ru/moskva/avtomobili/voyah_dream_8302635260'


def inputs():
    marketing = [''] * 19
    marketing[0], marketing[3], marketing[4] = 'Актуально', 'VOYAH DREAM', 'Business Jet'
    marketing[5:8] = [VIN, 15500000, 2026]
    feed = [''] * 29
    feed[0], feed[14], feed[15], feed[18], feed[28] = ID, f'<p>ID: {ID}</p>', 15500000, VIN, 2026
    source = {
        'cycle_id': 'cycle-1', 'authorization': 'VIN within workbook, unique_id for cards',
        'marketing': {'range': "'Размещение_автомобилей'!F29:X29", 'values': [marketing]},
        'feed_block': {'range': "'avito-feed-new'!A23:AC23", 'values': [feed]},
        'feed_tail': {'range': "'avito-feed-new'!A29:AC29", 'values': []},
        'new_feed_identities': {'values': [[ID]]}, 'used_feed_identities': {'values': []},
    }
    report = {'cycle_id': 'cycle-1', 'observed_cards': [{
        'source': 'avito', 'state': 'active', 'url': URL,
        'evidence': 'saved.png', 'evidence_manifest': 'saved.png.json',
        'card': {'placement_id_raw': 'VОDR011220262209260033', 'price': 15500000,
                 'year': 2026, 'vin': 'THIS_IS_NOT_AN_IDENTITY_ANCHOR'},
    }, {'source': 'avito', 'state': 'active', 'card': {'placement_id_raw': None}}]}
    return report, source


def test_vin_joins_only_workbook_rows_and_unique_id_identifies_card():
    report, source = inputs()
    binding, = validate_workbook_bindings(report, source, [ID])
    assert binding['marketing_row'] == 29
    assert binding['placement_id'] == ID
    assert binding['url'] == URL
    assert binding['basis'] == 'source_verified_registry_binding'
    assert binding['marketing_url'] == ''


@pytest.mark.parametrize('change', [
    'duplicate_vin', 'inactive', 'duplicate_feed_id', 'duplicate_card',
    'wrong_claim', 'missing_claim', 'wrong_description', 'price', 'year',
    'conflicting_url', 'conflicting_avito_id', 'no_authorization', 'wrong_cycle',
])
def test_ambiguous_or_conflicting_evidence_never_creates_binding(change):
    report, source = inputs()
    if change == 'duplicate_vin':
        source['marketing']['values'].append(deepcopy(source['marketing']['values'][0]))
    elif change == 'inactive':
        source['marketing']['values'][0][0] = 'Продано'
    elif change == 'duplicate_feed_id':
        source['used_feed_identities']['values'].append([ID])
    elif change == 'duplicate_card':
        report['observed_cards'].append(deepcopy(report['observed_cards'][0]))
    elif change == 'wrong_claim':
        report['observed_cards'][0]['card']['placement_id_raw'] = 'VODR011220262209260034'
    elif change == 'missing_claim':
        report['observed_cards'][0]['card']['placement_id_raw'] = None
    elif change == 'wrong_description':
        source['feed_block']['values'][0][14] = 'ID missing'
    elif change == 'price':
        source['marketing']['values'][0][6] = 15000000
    elif change == 'year':
        report['observed_cards'][0]['card']['year'] = 2025
    elif change == 'conflicting_url':
        source['marketing']['values'][0][18] = URL.replace('8302635260', '8479029041')
    elif change == 'conflicting_avito_id':
        source['feed_block']['values'][0][2] = '8479029041'
    elif change == 'no_authorization':
        source['authorization'] = ''
    elif change == 'wrong_cycle':
        source['cycle_id'] = 'another-cycle'
    with pytest.raises(ValueError):
        validate_workbook_bindings(report, source, [ID])
