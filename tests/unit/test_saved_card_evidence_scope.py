"""A valid screenshot of another page must never validate a saved advert."""
import copy
import hashlib
import json

import pytest

from app.service.automatic_content import automatic_content_review
from app.service.content_identity import resolve_content_identity
from app.service.evidence import EvidenceAccessError, read_direct_card_evidence
from app.service.placement_feed_snapshot import PlacementFeedSnapshot

UID = 'MBVC022220212508260023'
URL = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/'


def captured(tmp_path):
    image = b'local-synthetic-image'
    text = 'ID: ' + UID
    (tmp_path / 'card.png').write_bytes(image)
    manifest = {
        'schema_version': 'evidence.v1', 'source': 'auto_ru', 'purpose': 'direct_card',
        'page_number': 1, 'screenshot_file': 'card.png', 'screenshot_bytes': len(image),
        'screenshot_sha256': hashlib.sha256(image).hexdigest(),
        'requested_url_sha256': hashlib.sha256(URL.encode()).hexdigest(),
        'final_url_sha256': hashlib.sha256(URL.encode()).hexdigest(),
        'http_status': 200, 'captured_at': '2026-10-20T13:00:00Z',
    }
    card = {
        'source': 'auto_ru', 'url': URL, 'state': 'active', 'evidence': 'card.png',
        'card': {'placement_id': UID, 'description': text, 'description_capture': {
            'software_truncated': False, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
        }},
    }
    return card, manifest


@pytest.mark.parametrize('field,value', [
    ('source', 'avito'), ('purpose', 'search'), ('purpose', 'captcha_challenge'),
    ('requested_url_sha256', 'another-advert'), ('final_url_sha256', 'another-advert'),
    ('http_status', 403), ('http_status', 404),
])
def test_other_scope_cannot_supply_content_proof_or_exact_identity(tmp_path, field, value):
    saved, manifest = captured(tmp_path)
    manifest[field] = value
    (tmp_path / 'card.png.json').write_text(json.dumps(manifest))
    with pytest.raises(EvidenceAccessError):
        read_direct_card_evidence(saved, str(tmp_path))
    review = automatic_content_review({'cycle_id': 'test', 'observed_cards': [saved]}, tmp_path)
    assert 'content_evidence_unverified' in {f['code'] for f in review['cards'][0]['findings']}
    assert review['cards'][0]['captured_at_utc'] != manifest['captured_at']
    resolution = resolve_content_identity(saved, PlacementFeedSnapshot({}, {}, {}), evidence_dir=tmp_path)
    assert resolution['unique_id'] is None and resolution['status'] == 'evidence_unverified'


def test_matching_evidence_keeps_dated_exact_identity_without_live_claim(tmp_path):
    saved, manifest = captured(tmp_path)
    (tmp_path / 'card.png.json').write_text(json.dumps(manifest))
    before = copy.deepcopy(saved)
    result = resolve_content_identity(saved, PlacementFeedSnapshot({}, {}, {}), evidence_dir=tmp_path)
    assert result['unique_id'] == UID and result['basis'] == 'exact'
    review = automatic_content_review({'cycle_id': 'test', 'observed_cards': [saved]}, tmp_path)
    assert review['cards'][0]['captured_at_utc'] == manifest['captured_at']
    assert not review['new_live_scan'] and saved == before


def test_explicit_removal_can_have_404_but_active_card_cannot(tmp_path):
    saved, manifest = captured(tmp_path)
    saved['state'] = 'removed'
    manifest['http_status'] = 404
    (tmp_path / 'card.png.json').write_text(json.dumps(manifest))
    assert read_direct_card_evidence(saved, str(tmp_path))['http_status'] == 404
