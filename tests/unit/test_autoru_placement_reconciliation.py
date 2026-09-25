from app.scraper.base import evidence_manifest_name
from app.service.autoru_placement_reconciliation import OpenedAutoRuCard, reconcile_autoru_placements

PLACEMENT_ID = 'MBVC011220262508260027'
VIN = 'W1VVNLSZXS4493307'
OLD = 'https://auto.ru/cars/used/sale/mercedes/v_class/1132857121-old/'
NEW = 'https://auto.ru/cars/used/sale/mercedes/v_class/1132857122-new/'
OTHER = 'https://auto.ru/cars/used/sale/mercedes/v_class/1132857123-other/'
EVIDENCE = 'artifacts/evidence/auto_ru-card.png'


def _feed(*, action='show', vin=VIN):
    return [{'unique_id': PLACEMENT_ID, 'action': action, 'vin': vin}]


def _card(url=NEW, placement_id=PLACEMENT_ID, *, evidence=True, state='active'):
    result = {'state': state, 'card': {'placement_id': placement_id}}
    if evidence:
        result.update(evidence=EVIDENCE, evidence_manifest=evidence_manifest_name(EVIDENCE))
    return OpenedAutoRuCard(url=url, inspection=result)


def test_exact_active_id_at_new_url_is_a_reviewable_republication_candidate():
    findings = reconcile_autoru_placements(_feed(), [_card()], current_urls_by_vin={VIN: OLD})

    assert len(findings) == 1
    assert findings[0].code == 'republication_candidate'
    assert findings[0].current_url == OLD
    assert findings[0].observed_urls == (NEW,)


def test_duplicate_public_ids_cannot_choose_a_new_link():
    findings = reconcile_autoru_placements(_feed(), [_card(NEW), _card(OTHER)], current_urls_by_vin={VIN: OLD})

    assert findings[0].code == 'duplicate_public_id'
    assert set(findings[0].observed_urls) == {NEW, OTHER}


def test_hidden_feed_row_with_public_card_is_flagged():
    findings = reconcile_autoru_placements(_feed(action='hide'), [_card()])

    assert findings[0].code == 'hidden_but_public'


def test_no_card_or_missing_evidence_never_proves_absence():
    missing = reconcile_autoru_placements(_feed(), [], catalogue_complete=True)
    unproven = reconcile_autoru_placements(_feed(), [_card(evidence=False)])

    assert missing[0].code == 'not_verified'
    assert [item.code for item in unproven] == ['card_evidence_missing', 'not_verified']


def test_missing_vin_does_not_assign_a_registry_link():
    findings = reconcile_autoru_placements(_feed(vin=None), [_card()], current_urls_by_vin={VIN: OLD})

    assert findings[0].code == 'vehicle_anchor_missing'


def test_duplicate_feed_id_is_not_used_for_a_link_decision():
    findings = reconcile_autoru_placements(_feed() * 2, [_card()], current_urls_by_vin={VIN: OLD})

    assert [item.code for item in findings] == ['duplicate_feed_id', 'duplicate_feed_id']


def test_text_section_divider_is_not_reported_as_invalid_car_id():
    rows = [
        {'car': '<car></car>', 'unique_id': PLACEMENT_ID, 'action': 'show', 'vin': VIN},
        {'car': 'АВТОМОБИЛИ ЗА ЛИНИЕЙ СНЯТЫ С ПРОДАЖИ И УДАЛЕНЫ ИЗ ФИДА ДАННЫХ'},
        {'car': '<car></car>', 'unique_id': 'MBVC011220252508260004',
         'action': 'hide', 'vin': 'W1VVNLTZ5S4556796'},
    ]

    findings = reconcile_autoru_placements(rows, [], first_data_row=39)

    assert [item.code for item in findings] == ['not_verified', 'not_verified']
    assert [item.feed_row for item in findings] == [39, 41]


def test_car_row_without_id_still_fails_closed_after_divider_fix():
    findings = reconcile_autoru_placements(
        [{'car': '<car></car>', 'unique_id': '', 'action': 'show', 'vin': VIN}], [],
    )

    assert [item.code for item in findings] == ['invalid_feed_id']
