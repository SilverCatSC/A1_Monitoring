import json
from types import SimpleNamespace

import pytest

from app.models import EngineType, ObservationState
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.service.catalogue_visibility import add_catalogue_visibility
from app.service.filters import CANONICAL_FILTERS
from app.service.placement_status import outside_search_label, resolved_placement_status

URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_8176328839'


@pytest.mark.parametrize('code', ['sold', 'closed', 'unpublished'])
def test_fresh_evidenced_removal_overrides_earlier_active_catalogue(code):
    details = {'direct_inspection': {'state': 'removed', 'status_code': code,
        'evidence': 'direct.png', 'evidence_manifest': evidence_manifest_name('direct.png')}}
    assert resolved_placement_status(EngineType.AVITO, URL, URL, details) == 'inactive'
    assert resolved_placement_status(EngineType.AVITO, URL, URL.replace('8839', '8840'), details) is None
    for missing in ['evidence', 'evidence_manifest']:
        changed = {'direct_inspection': {k: v for k, v in details['direct_inspection'].items() if k != missing}}
        assert resolved_placement_status(EngineType.AVITO, URL, URL, changed) is None
    for state, status in [('unknown', 'http_error'), ('blocked', 'blocked'), ('removed', 'http_error')]:
        changed = {'direct_inspection': {**details['direct_inspection'], 'state': state, 'status_code': status}}
        assert resolved_placement_status(EngineType.AVITO, URL, URL, changed) is None


@pytest.mark.parametrize('filter_key,url', [
    ('auto_vle_new', 'https://auto.ru/cars/new/group/mercedes/vle/25032437/25069598/1133252498-92c0e2e5/'),
    ('auto_maybach_s_new', 'https://auto.ru/cars/new/group/mercedes/s_class_maybach/1/2/1133252498-a/'),
    ('avito_zeekr_9x_new', 'https://www.avito.ru/moskva/avtomobili/zeekr_9x_8176328839'),
])
def test_narrow_or_unproven_condition_filter_miss_does_not_assert_five_plus(tmp_path, filter_key, url):
    definition = next(d for d in CANONICAL_FILTERS if d.key == filter_key)
    folder = tmp_path / 'cycles' / 'cycle'
    folder.mkdir(parents=True)
    collection = {'cycle_id': 'cycle', 'results': [{'source': definition.source.value, 'filter_id': 'filter',
        'url': definition.url, 'pages_limit': 5, 'result': {'complete': True, 'hits': []}}]}
    (folder / 'search_collection.json').write_text(json.dumps(collection))
    report = {'cycle_id': 'cycle', 'observed_cards': [{'source': definition.source.value, 'url': url, 'state': 'active'}]}
    add_catalogue_visibility(report, tmp_path)
    assert report['observed_cards'][0]['search_positions'][0]['label'] == 'Применимость фильтра не подтверждена'
    collection['results'][0]['result']['hits'] = [{'url': url, 'page_number': 1, 'position': 1}]
    (folder / 'search_collection.json').write_text(json.dumps(collection))
    add_catalogue_visibility(report, tmp_path)
    assert report['observed_cards'][0]['search_positions'][0]['label'] == 'Стр. 1, место 1'


@pytest.mark.parametrize('source,url', [
    (EngineType.AVITO, URL),
    (EngineType.AUTO_RU, 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1234567890-a/'),
])
def test_expectation_follows_link_and_complete_catalogue(source, url):
    complete = {'catalogue_complete': True, 'inspection_skip_reason': 'not_in_active_catalogue'}
    assert resolved_placement_status(source, None, None, {}) == 'not_expected'
    assert resolved_placement_status(source, url, url, complete) == 'inactive'
    assert resolved_placement_status(source, 'broken-link', None, complete) is None
    assert resolved_placement_status(source, url, None, complete) is None
    assert resolved_placement_status(source, url, url, {**complete, 'catalogue_complete': False}) is None
    for reason in ['source_unavailable', 'catalogue_incomplete', 'identity_ambiguous']:
        assert resolved_placement_status(source, url, url, {**complete, 'inspection_skip_reason': reason}) is None
    for state in ['blocked', 'unknown', 'active']:
        assert resolved_placement_status(source, url, url, {**complete, 'direct_inspection': {'state': state}}) is None


@pytest.mark.parametrize('case,expected', [
    ('complete', '5+'), ('short_search', '5+'), ('blocked', None), ('incomplete', None),
    ('old_cycle', None), ('different_card', None), ('not_checked', None),
])
def test_depth_label_requires_same_cycle_active_card_and_complete_search(case, expected):
    record = SimpleNamespace(source=EngineType.AVITO, url=URL, cycle_id='cycle',
                             details={'direct_inspection': {'state': 'active'}})
    observation = SimpleNamespace(state=ObservationState.ABSENT_UNCERTAIN,
        scan_run=SimpleNamespace(cycle_id='cycle'),
        raw_payload={'expected_listing_key': canonical_listing_key(EngineType.AVITO, URL),
                     'scan_diagnostics': {'search_complete': True, 'search_pages_limit': 5}})
    if case == 'blocked':
        record.details['direct_inspection']['state'] = 'blocked'
    if case == 'incomplete':
        observation.raw_payload['scan_diagnostics']['search_complete'] = False
    if case == 'old_cycle':
        record.cycle_id = 'older'
    if case == 'different_card':
        observation.raw_payload['expected_listing_key'] = 'avito:88888888'
    if case == 'not_checked':
        observation.state = ObservationState.REVIEW_REQUIRED
    assert outside_search_label(observation, record) == expected


def test_catalogue_positions_include_cards_without_registry_links(tmp_path):
    definition = next(d for d in CANONICAL_FILTERS if d.key == 'avito_v_class_all')
    folder = tmp_path / 'cycles' / 'cycle'
    folder.mkdir(parents=True)
    collection = {'cycle_id': 'cycle', 'results': [{'source': 'avito', 'filter_id': 'filter',
        'url': definition.url, 'pages_limit': 5,
        'result': {'complete': True, 'hits': [{'url': URL, 'page_number': 4, 'position': 46}]}}]}
    (folder / 'search_collection.json').write_text(json.dumps(collection))
    report = {'cycle_id': 'cycle', 'observed_cards': [
        {'source': 'avito', 'url': URL, 'state': 'active'},
        {'source': 'avito', 'url': URL.replace('8176328839', '8176328840'), 'state': 'active'}]}
    add_catalogue_visibility(report, tmp_path)
    assert report['observed_cards'][0]['search_positions'][0]['label'] == 'Стр. 4, место 46'
    assert report['observed_cards'][1]['search_positions'][0]['label'] == '5+'
    collection['results'][0]['result']['complete'] = False
    (folder / 'search_collection.json').write_text(json.dumps(collection))
    add_catalogue_visibility(report, tmp_path)
    assert report['observed_cards'][1]['search_positions'][0]['label'] == 'Поиск не завершён'


def test_followup_positions_do_not_replace_sealed_search_collection(tmp_path):
    definition = next(d for d in CANONICAL_FILTERS if d.key == 'avito_v_class_all')
    folder = tmp_path / 'cycles' / 'cycle'
    folder.mkdir(parents=True)
    sealed = folder / 'search_collection.json'
    sealed.write_text('{"cycle_id":"cycle","results":[]}')
    before = sealed.read_bytes()
    collection = {'cycle_id': 'cycle', 'results': [{'source': 'avito', 'filter_id': 'filter',
        'url': definition.url, 'pages_limit': 5, 'result': {'complete': True,
        'hits': [{'url': URL, 'page_number': 2, 'position': 8}]}}]}
    report = {'cycle_id': 'cycle', 'observed_cards': [{'source': 'avito', 'url': URL, 'state': 'active'}]}
    add_catalogue_visibility(report, tmp_path, collection=collection)
    assert report['observed_cards'][0]['search_positions'][0]['label'] == 'Стр. 2, место 8'
    assert sealed.read_bytes() == before
    collection['cycle_id'] = 'another-cycle'
    untouched = {'cycle_id': 'cycle', 'observed_cards': [{'source': 'avito', 'url': URL, 'state': 'active'}]}
    add_catalogue_visibility(untouched, tmp_path, collection=collection)
    assert 'search_positions' not in untouched['observed_cards'][0]
