import pytest

from app.scraper.base import evidence_manifest_name
from app.service.marketplace_placement_reconciliation import (
    OpenedMarketplaceCard,
    reconcile_avito_placements,
)

PLACEMENT_ID = 'MBVC011220262508260027'
VIN = 'W1VVNLSZXS4493307'
OLD = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_8047929828'
NEW = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_8176281881'
OTHER = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_8176281882'
EVIDENCE = 'artifacts/evidence/avito-card.png'
FIRST_ROWS = {'avito-feed-new': 3, 'avito-feed-used': 4}
IDS_TO_OLD = {PLACEMENT_ID: OLD, 'MBVC011220262508260009': OLD}


def _feeds(*, new=None, used=None):
    return {'avito-feed-new': new or [], 'avito-feed-used': used or []}


def _card(url=NEW, placement_id=PLACEMENT_ID, *, evidence=True, state='active'):
    inspection = {'state': state, 'card': {'placement_id': placement_id}}
    if evidence:
        inspection.update(evidence=EVIDENCE, evidence_manifest=evidence_manifest_name(EVIDENCE))
    return OpenedMarketplaceCard(url=url, inspection=inspection)


def test_same_id_at_new_avito_url_is_reviewable_republication_candidate():
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8047929828'}]),
        [_card()], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert len(findings) == 1
    assert findings[0].code == 'republication_candidate'
    assert findings[0].feed_sheet == 'avito-feed-new'
    assert findings[0].feed_row == 3
    assert findings[0].observed_urls == (NEW,)


def test_duplicate_id_across_new_and_used_feeds_cannot_choose_link():
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN}], used=[{'Id': PLACEMENT_ID, 'VIN': VIN}]),
        [_card()], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['duplicate_feed_id', 'duplicate_feed_id']
    assert {item.feed_sheet for item in findings} == {'avito-feed-new', 'avito-feed-used'}


def test_missing_vin_does_not_block_a_verified_id_link():
    findings = reconcile_avito_placements(
        _feeds(used=[{'Id': PLACEMENT_ID}]), [_card()],
        first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert findings[0].code == 'republication_candidate'
    assert findings[0].feed_sheet == 'avito-feed-used'


def test_unproven_card_and_wrong_marketplace_url_cannot_confirm_identity():
    feed = _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN}])
    no_evidence = reconcile_avito_placements(feed, [_card(evidence=False)], first_data_rows=FIRST_ROWS)
    wrong_marketplace = reconcile_avito_placements(
        feed,
        [_card(url='https://auto.ru/cars/used/sale/mercedes/v_class/1132857122-new/')],
        first_data_rows=FIRST_ROWS,
    )

    assert [item.code for item in no_evidence] == ['card_evidence_missing', 'not_verified']
    assert [item.code for item in wrong_marketplace] == ['not_verified']


def test_invalid_avito_customer_id_does_not_match_platform_id():
    findings = reconcile_avito_placements(
        _feeds(used=[{'Id': '8176281881', 'AvitoId': '8176281881', 'VIN': VIN}]),
        [_card()], first_data_rows=FIRST_ROWS,
    )

    assert [item.code for item in findings] == ['invalid_feed_id', 'public_id_without_feed_row']


def test_both_feed_tabs_are_required_for_cross_tab_uniqueness():
    with pytest.raises(ValueError, match='both Avito feed tabs'):
        reconcile_avito_placements(
            {'avito-feed-new': [{'Id': PLACEMENT_ID}]}, [_card()], first_data_rows=FIRST_ROWS,
        )


def test_mixed_cyrillic_latin_card_id_can_identify_republication_with_warning():
    card = OpenedMarketplaceCard(
        url=NEW,
        inspection={
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        },
    )
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': 'MBVC011220262508260009', 'VIN': VIN}]),
        [card], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['mixed_script_id', 'republication_candidate']
    assert findings[0].suggested_placement_id == 'MBVC011220262508260009'
    assert findings[0].current_url == OLD
    assert findings[0].feed_sheet == 'avito-feed-new'
    assert findings[0].observed_urls == (NEW,)
    assert findings[1].id_match_basis == 'visual_alias'
    assert findings[1].observed_urls == (NEW,)


def test_owner_sample_avito_id_confirms_mixed_script_card_match():
    card = OpenedMarketplaceCard(
        url=NEW,
        inspection={
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        },
    )
    findings = reconcile_avito_placements(
        _feeds(new=[{
            'Id': 'MBVC011220262508260009', 'VIN': VIN, 'AvitoId': '8176281881',
        }]),
        [card], first_data_rows={'avito-feed-new': 9, 'avito-feed-used': 4},
        current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['mixed_script_id', 'republication_candidate']
    assert findings[0].feed_row == 9
    assert findings[0].platform_id_confirmed
    assert findings[1].platform_id_confirmed
    assert findings[1].id_match_basis == 'visual_alias'


def test_missing_card_id_cannot_use_stale_prone_platform_id_as_sole_identity():
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8176281881'}]),
        [_card(placement_id=None)], first_data_rows=FIRST_ROWS,
        current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['platform_id_candidate', 'not_verified']
    assert findings[0].id_match_basis == 'platform_id'
    assert findings[0].platform_id_confirmed
    assert findings[1].observed_urls == ()


def test_unrepairable_card_id_is_only_a_platform_id_candidate():
    card = OpenedMarketplaceCard(
        url=NEW,
        inspection={
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'BAD-CARD-ID'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        },
    )
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8176281881'}]),
        [card], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['platform_id_candidate', 'not_verified']
    assert findings[0].placement_id == 'BAD-CARD-ID'
    assert findings[0].suggested_placement_id == PLACEMENT_ID
    assert findings[1].observed_urls == ()


def test_republication_with_new_avito_id_still_matches_visual_alias():
    card = OpenedMarketplaceCard(
        url=NEW,
        inspection={
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        },
    )
    findings = reconcile_avito_placements(
        _feeds(new=[{
            'Id': 'MBVC011220262508260009', 'VIN': VIN, 'AvitoId': '8047929828',
        }]),
        [card], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['mixed_script_id', 'republication_candidate']
    assert findings[1].id_match_basis == 'visual_alias'
    assert not findings[1].platform_id_confirmed


def test_description_id_conflicting_with_platform_id_fails_closed():
    findings = reconcile_avito_placements(
        _feeds(new=[
            {'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8176281881'},
            {'Id': 'MBVC011220262508260009', 'VIN': 'W1VVNLTZ4T4788708'},
        ]),
        [_card(placement_id='MBVC011220262508260009')],
        first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == [
        'identity_conflict', 'not_verified', 'not_verified',
    ]
    assert findings[0].suggested_placement_id == PLACEMENT_ID


def test_duplicate_avito_id_across_feeds_blocks_platform_anchor():
    findings = reconcile_avito_placements(
        _feeds(
            new=[{'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8176281881'}],
            used=[{
                'Id': 'MBVC011220262508260009', 'VIN': 'W1VVNLTZ4T4788708',
                'AvitoId': '8176281881',
            }],
        ),
        [_card(placement_id=None)], first_data_rows=FIRST_ROWS,
    )

    assert [item.code for item in findings] == [
        'duplicate_platform_id', 'duplicate_platform_id', 'card_id_missing',
    ]


def test_platform_id_without_direct_card_evidence_does_not_verify():
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8176281881'}]),
        [_card(placement_id=None, evidence=False)], first_data_rows=FIRST_ROWS,
    )

    assert [item.code for item in findings] == ['not_verified']


def test_mixed_script_id_at_current_link_does_not_create_false_republication():
    card = OpenedMarketplaceCard(
        url=OLD,
        inspection={
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        },
    )
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': 'MBVC011220262508260009', 'VIN': VIN}]),
        [card], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['mixed_script_id', 'link_current']
    assert findings[1].id_match_basis == 'visual_alias'


def test_visual_alias_without_exact_feed_id_stays_invalid():
    card = OpenedMarketplaceCard(
        url=NEW,
        inspection={
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        },
    )
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': PLACEMENT_ID, 'VIN': VIN}]),
        [card], first_data_rows=FIRST_ROWS,
    )

    assert [item.code for item in findings] == ['invalid_card_id', 'not_verified']


def test_mixed_script_feed_id_can_match_an_exact_card_without_marketing_fix():
    findings = reconcile_avito_placements(
        _feeds(new=[{'Id': 'МBVC011220262508260027', 'VIN': VIN}]),
        [_card()], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == ['mixed_script_feed_id', 'republication_candidate']
    assert findings[0].suggested_placement_id == PLACEMENT_ID
    assert findings[1].id_match_basis == 'visual_alias'


def test_duplicate_after_visual_normalization_blocks_link_decision():
    findings = reconcile_avito_placements(
        _feeds(
            new=[{'Id': 'МBVC011220262508260027', 'VIN': VIN}],
            used=[{'Id': PLACEMENT_ID, 'VIN': VIN}],
        ),
        [_card()], first_data_rows=FIRST_ROWS, current_urls_by_placement_id=IDS_TO_OLD,
    )

    assert [item.code for item in findings] == [
        'mixed_script_feed_id', 'duplicate_feed_id', 'duplicate_feed_id'
    ]
