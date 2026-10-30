import copy
import hashlib
import json

import pytest

from app.scraper.base import EVIDENCE_SCHEMA_VERSION, evidence_manifest_name
from app.service.catalogue_visibility import add_catalogue_visibility
from app.service.search_followup import apply_search_followup, save_search_followup
from app.service.targeted_search import targeted_filter


def fixture(tmp_path):
    root = tmp_path / 'evidence'
    base = root / 'cycles/cycle'
    base.mkdir(parents=True)
    definition = targeted_filter('avito_sprinter_all')
    url = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_sprinter_1234567890'
    report = {'cycle_id': 'cycle', 'created_at_utc': '2026-10-30T08:00:00+00:00',
              'findings': [], 'observed_cards': [{'source': 'avito', 'url': url, 'state': 'active',
                  'unique_id': 'MBSP011220262209260037', 'card': {'price': 41500000},
                  'search_positions': [{'label': 'Поиск не завершён'}]}]}
    raw = json.dumps(report).encode()
    (base / 'placement_reconciliation.json').write_bytes(raw)
    entry = {'source': 'avito', 'filter_id': 'sprinter', 'url': definition.url, 'pages_limit': 5,
             'version': 1, 'result': {'complete': False, 'hits': [], 'page_count': 0}}
    (base / 'search_collection.json').write_text(json.dumps({'cycle_id': 'cycle', 'results': [entry]}))
    image = root / 'search.png'
    image.write_bytes(b'fixture screenshot')
    stamp = '2026-10-30T09:00:00+00:00'
    manifest = {'schema_version': EVIDENCE_SCHEMA_VERSION, 'source': 'avito', 'purpose': 'search_page',
                'page_number': 1, 'screenshot_file': image.name, 'screenshot_bytes': image.stat().st_size,
                'screenshot_sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'captured_at': stamp,
                'requested_url_sha256': hashlib.sha256(definition.url.encode()).hexdigest(),
                'final_url_sha256': hashlib.sha256(definition.url.encode()).hexdigest(), 'http_status': 200}
    (root / evidence_manifest_name(image.name)).write_text(json.dumps(manifest))
    result = {'complete': True, 'error': None, 'page_count': 1, 'exhausted': True,
              'scanned_at': stamp, 'hits': [{'url': url, 'page_number': 1, 'position': 7}],
              'diagnostics': {'page_1_evidence': image.name, 'page_1_http_status': 200,
                              'page_1_final_url': definition.url}}
    record = {'kind': 'targeted_search', 'filter_key': definition.key, 'source': 'avito',
              'url': definition.url, 'pages_limit': 5, 'scope_errors': [],
              'writes_monitoring_cycle': False, 'created_at_utc': stamp, 'result': result}
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps({'kind': 'targeted_search_batch', 'writes_monitoring_cycle': False,
                                'records': [record]}))
    return root, base, report, batch


def test_followup_updates_positions_without_altering_identity_or_original_files(tmp_path):
    root, base, report, batch = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in base.iterdir()}
    view = save_search_followup(base / 'placement_reconciliation.json', batch, root)
    assert all((base / name).read_bytes() == raw for name, raw in before.items())
    card = view['observed_cards'][0]
    assert card['unique_id'] == report['observed_cards'][0]['unique_id']
    assert card['card'] == report['observed_cards'][0]['card']
    assert card['search_positions'][0]['label'] == 'Стр. 1, место 7'
    assert card['search_positions'][0]['post_run'] is True
    assert card['original_search_positions'] == report['observed_cards'][0]['search_positions']
    readback = copy.deepcopy(report)
    apply_search_followup(readback, root)
    assert readback == view
    # The HTML route projects the original collection again after reading.
    add_catalogue_visibility(readback, root)
    assert readback == view


@pytest.mark.parametrize('damage', ['base_hash', 'screenshot', 'already_complete', 'wrong_radius', 'incomplete'])
def test_unverified_followup_cannot_replace_saved_positions(tmp_path, damage):
    root, base, report, batch = fixture(tmp_path)
    if damage == 'screenshot':
        (root / 'search.png').write_bytes(b'changed')
    elif damage == 'base_hash':
        save_search_followup(base / 'placement_reconciliation.json', batch, root)
        (base / 'placement_reconciliation.json').write_text('{}')
        with pytest.raises(ValueError):
            apply_search_followup(report, root)
        assert report['observed_cards'][0]['search_positions'][0]['label'] == 'Поиск не завершён'
        return
    elif damage == 'already_complete':
        p = base / 'search_collection.json'
        data = json.loads(p.read_text())
        data['results'][0]['result']['complete'] = True
        p.write_text(json.dumps(data))
    else:
        data = json.loads(batch.read_text())
        if damage == 'incomplete':
            data['records'][0]['result']['complete'] = False
        else:
            data['records'][0]['url'] = data['records'][0]['url'].replace('radius=0', 'radius=100')
        batch.write_text(json.dumps(data))
    with pytest.raises((ValueError, RuntimeError)):
        save_search_followup(base / 'placement_reconciliation.json', batch, root)
    assert not (base / 'search_followup/ready.json').exists()
