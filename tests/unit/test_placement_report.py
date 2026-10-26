import hashlib
import json
from copy import deepcopy

import pytest

from app.models import MonitoringCycle
from app.service.placement_report import PlacementReportError, read_cycle_placement_report
from app.service.targeted_search import targeted_filter


def _cycle(cycle_id='cycle-1'):
    return MonitoringCycle(
        id=cycle_id,
        summary={
            'placement_reconciliation': {
                'report_path': f'cycles/{cycle_id}/placement_reconciliation.json',
            }
        },
    )


def _write_report(root, cycle_id='cycle-1', **changes):
    target = root / 'cycles' / cycle_id / 'placement_reconciliation.json'
    target.parent.mkdir(parents=True)
    payload = {'schema_version': 1, 'cycle_id': cycle_id, 'findings': [], 'observed_cards': []} | changes
    target.write_text(json.dumps(payload))
    return target


def test_reader_accepts_only_report_for_its_cycle(tmp_path):
    _write_report(tmp_path)
    assert read_cycle_placement_report(_cycle(), str(tmp_path))['cycle_id'] == 'cycle-1'
    with pytest.raises(PlacementReportError, match='report_not_available'):
        read_cycle_placement_report(MonitoringCycle(id='cycle-1', summary={}), str(tmp_path))
    with pytest.raises(PlacementReportError, match='report_not_available'):
        read_cycle_placement_report(
            MonitoringCycle(
                id='cycle-1',
                summary={
                    'placement_reconciliation': {
                        'report_path': '../placement_reconciliation.json',
                    }
                },
            ),
            str(tmp_path),
        )


def test_reader_rejects_wrong_cycle_and_symlink(tmp_path):
    target = _write_report(tmp_path, cycle_id='cycle-1', findings=[{'code': 'not_verified'}])
    target.unlink()
    target.symlink_to(tmp_path / 'outside.json')
    with pytest.raises(PlacementReportError, match='invalid_report_path'):
        read_cycle_placement_report(_cycle(), str(tmp_path))


def test_reader_rejects_wrong_embedded_cycle_id(tmp_path):
    target = _write_report(tmp_path, cycle_id='cycle-1')
    payload = json.loads(target.read_text())
    payload['cycle_id'] = 'other'
    target.write_text(json.dumps(payload))
    with pytest.raises(PlacementReportError, match='invalid_report_schema'):
        read_cycle_placement_report(_cycle(), str(tmp_path))


def test_reader_rejects_malformed_card_url_shape(tmp_path):
    _write_report(tmp_path, observed_cards=[{'source': 'avito', 'url': ['not-a-url']}])
    with pytest.raises(PlacementReportError, match='invalid_report_schema'):
        read_cycle_placement_report(_cycle(), str(tmp_path))


def _business_review(report_file):
    return {
        'schema_version': 1,
        'cycle_id': 'cycle-1',
        'base_report_sha256': hashlib.sha256(report_file.read_bytes()).hexdigest(),
        'reviewed_at_utc': '2026-10-15T00:00:00+00:00',
        'entries': [
            {
                'title': 'Sprinter',
                'status': 'У партнёра Grades',
                'explanation': 'В нашем каталоге отсутствует.',
                'action': 'Зафиксировать ссылку партнёра.',
                'basis': 'Сообщение владельца.',
            }
        ],
        'vclass_cards': [
            {
                'url': 'https://www.avito.ru/moskva/avtomobili/mercedes_8271920707',
                'placement_id': 'MBVC011220262508260030',
                'design': 'Индивидуальный салон',
                'mapping': 'Кандидат, связь не подтверждена.',
            }
        ],
    }


def test_business_review_preserves_original_report_and_distinguishes_partner_status(tmp_path):
    original = _write_report(tmp_path)
    raw = original.read_bytes()
    cycle = _cycle()
    review = _business_review(original)
    cycle.summary['post_run_business_review'] = review
    report = read_cycle_placement_report(cycle, str(tmp_path))
    assert report['business_review'] == review
    assert report['findings'] == []
    assert original.read_bytes() == raw


@pytest.mark.parametrize('change', ['cycle', 'report_hash', 'url', 'entry', 'card'])
def test_business_review_rejects_cross_cycle_tamper_and_unsafe_links(tmp_path, change):
    original = _write_report(tmp_path)
    cycle = _cycle()
    review = deepcopy(_business_review(original))
    if change == 'cycle':
        review['cycle_id'] = 'another-cycle'
    elif change == 'report_hash':
        review['base_report_sha256'] = '0' * 64
    elif change == 'url':
        review['vclass_cards'][0]['url'] = 'javascript:alert(1)'
    elif change == 'entry':
        review['entries'][0]['basis'] = None
    elif change == 'card':
        review['vclass_cards'][0] = None
    cycle.summary['post_run_business_review'] = review
    with pytest.raises(PlacementReportError, match='invalid_business_review'):
        read_cycle_placement_report(cycle, str(tmp_path))


@pytest.mark.parametrize('basis', ['owner_confirmed_registry_binding', 'source_verified_registry_binding'])
def test_owner_binding_resolves_only_matching_id_and_platform_card(tmp_path, basis):
    findings = [
        {
            'code': 'vehicle_anchor_missing',
            'placement_id': 'MBVC011220262508260030',
            'observed_urls': ['https://www.avito.ru/moskva/avtomobili/mercedes_8271920707?context=old'],
        },
        {
            'code': 'vehicle_anchor_missing',
            'placement_id': 'MBVC011220262508260031',
            'observed_urls': ['https://www.avito.ru/moskva/avtomobili/mercedes_8271920707'],
        },
        {
            'code': 'vehicle_anchor_missing',
            'placement_id': 'MBVC011220262508260030',
            'observed_urls': ['https://www.avito.ru/moskva/avtomobili/mercedes_1234567890'],
        },
    ]
    original = _write_report(tmp_path, findings=findings)
    raw = original.read_bytes()
    review = _business_review(original)
    review['confirmed_registry_bindings'] = [
        {
            'placement_id': 'MBVC011220262508260030',
            'listing_id': 'confirmed-listing',
            'url': 'https://www.avito.ru/moskva/avtomobili/mercedes_8271920707',
            'basis': basis,
        }
    ]
    cycle = _cycle()
    cycle.summary['post_run_business_review'] = review
    report = read_cycle_placement_report(cycle, str(tmp_path))
    assert report['findings'][0]['post_run_resolution'] == basis
    assert all('post_run_resolution' not in f for f in report['findings'][1:])
    assert original.read_bytes() == raw


def _reviewed_search(tmp_path, change=None):
    definition = targeted_filter('avito_voyah_dream_all')
    url = 'https://www.avito.ru/moskva/avtomobili/voyah_dream_8302635260'
    placement_id = 'VODR011220262209260033'
    original = _write_report(tmp_path, observed_cards=[{'source': 'avito', 'url': url, 'state': 'active'}])
    raw = original.read_bytes()
    review = _business_review(original)
    review['confirmed_registry_bindings'] = [
        {'placement_id': placement_id, 'listing_id': 'listing', 'url': url}
    ]
    record = {
        'schema_version': 1,
        'kind': 'targeted_search',
        'record_id': 'targeted-1',
        'created_at_utc': '2026-10-15T12:04:49+00:00',
        'source': 'avito',
        'filter_key': definition.key,
        'url': definition.url,
        'writes_monitoring_cycle': False,
        'scope_errors': [],
        'result': {
            'complete': True,
            'page_count': 4,
            'error': None,
            'hits': [
                {
                    'url': url + '?context=saved',
                    'page_number': 1,
                    'position': 36,
                    'raw': {'raw_text': 'ID: Vodr011220262209260033.'},
                }
            ],
            'diagnostics': {
                'page_1_http_status': 200,
                'page_1_final_url': definition.url.replace('&localPriority=0', ''),
            },
        },
    }
    relative = 'cycles/cycle-1/business_review/voyah_search_result.json'
    path = tmp_path / relative
    path.parent.mkdir()
    if change == 'identity':
        record['result']['hits'][0]['raw']['raw_text'] = 'ID: VODR011220262209260034.'
    elif change == 'scope':
        record['result']['diagnostics']['page_1_final_url'] = 'https://www.avito.ru/moskva/avtomobili'
    elif change == 'incomplete':
        record['result']['complete'] = False
    elif change == 'blocked':
        record['result']['diagnostics']['page_1_http_status'] = 429
    elif change == 'different_card':
        record['result']['hits'][0]['url'] = url.replace('8302635260', '8479029041')
    elif change == 'invalid_position':
        record['result']['hits'][0]['position'] = 0
    path.write_text(json.dumps(record))
    review['voyah_search_review'] = {
        'cycle_id': 'cycle-1',
        'complete': True,
        'record_id': 'targeted-1',
        'result_copy': relative,
        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    if change == 'tamper':
        path.write_text(path.read_text() + ' ')
    elif change == 'cross_cycle':
        review['voyah_search_review']['cycle_id'] = 'cycle-2'
    elif change == 'outside_path':
        review['voyah_search_review']['result_copy'] = '../outside.json'
    elif change == 'symlink':
        outside = tmp_path / 'outside.json'
        path.rename(outside)
        path.symlink_to(outside)
    cycle = _cycle()
    cycle.summary['post_run_business_review'] = review
    return cycle, original, raw, path


def test_dated_targeted_positions_do_not_rewrite_original_cycle(tmp_path):
    cycle, original, raw, result = _reviewed_search(tmp_path)
    saved_result = result.read_bytes()
    report = read_cycle_placement_report(cycle, str(tmp_path))
    position = report['observed_cards'][0]['supplemental_search_positions'][0]
    assert position['label'] == 'Стр. 1, место 36'
    assert position['placement_id'] == 'VODR011220262209260033'
    assert position['record_id'] == 'targeted-1' and position['post_run'] is True
    assert position['checked_at_utc'] == '2026-10-15T12:04:49+00:00'
    assert 'search_positions' not in report['observed_cards'][0]
    assert original.read_bytes() == raw and result.read_bytes() == saved_result


@pytest.mark.parametrize(
    'change',
    [
        'identity',
        'scope',
        'incomplete',
        'blocked',
        'different_card',
        'invalid_position',
        'tamper',
        'cross_cycle',
        'outside_path',
        'symlink',
    ],
)
def test_unproven_targeted_search_does_not_supply_positions(tmp_path, change):
    cycle, _, _, _ = _reviewed_search(tmp_path, change)
    report = read_cycle_placement_report(cycle, str(tmp_path))
    assert 'supplemental_search_positions' not in report['observed_cards'][0]


def _publication_review_alerts(source='auto_ru'):
    from app.scraper.base import evidence_manifest_name
    from app.service.publication_alerts import publication_alerts

    url = (
        'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'
        if source == 'auto_ru'
        else 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_8271920707'
    )
    return publication_alerts(
        {
            'cycle_id': 'cycle-1',
            'catalogue_enumeration_complete': {source: False},
            'observed_cards': [
                {
                    'source': source,
                    'url': url,
                    'state': 'removed',
                    'card': {},
                    'evidence': 'card.png',
                    'evidence_manifest': evidence_manifest_name('card.png'),
                }
            ],
        }
    )


def test_targeted_alert_review_preserves_original_cards_and_other_source_signals(tmp_path):
    target = _write_report(tmp_path, publication_alerts=_publication_review_alerts('avito'))
    before = target.read_bytes()
    cycle = _cycle()
    review = _business_review(target)
    review['publication_alerts'] = _publication_review_alerts()
    cycle.summary = {**cycle.summary, 'post_run_business_review': review}
    r = read_cycle_placement_report(cycle, str(tmp_path))
    assert r['observed_cards'] == [] and target.read_bytes() == before
    assert {a['source'] for a in r['publication_alerts']['items']} == {'auto_ru', 'avito'}
    assert r['publication_alerts']['total'] == 2


@pytest.mark.parametrize('mutation', ['cycle', 'url', 'counts'])
def test_targeted_alert_review_rejects_invalid_payload(tmp_path, mutation):
    target = _write_report(tmp_path)
    cycle = _cycle()
    review = _business_review(target)
    alerts = _publication_review_alerts()
    if mutation == 'cycle':
        alerts['cycle_id'] = 'other'
    elif mutation == 'url':
        alerts['items'][0]['old_url'] = 'javascript:alert(1)'
    else:
        alerts['counts'] = {}
    review['publication_alerts'] = alerts
    cycle.summary = {**cycle.summary, 'post_run_business_review': review}
    with pytest.raises(PlacementReportError, match='invalid_publication_alert_review'):
        read_cycle_placement_report(cycle, str(tmp_path))
