import hashlib
import json

import pytest

from app.service.content_identity import resolve_content_identity
from app.service.placement_feed_snapshot import PlacementFeedSnapshot

UID = 'MBVC022220212508260023'
ALIAS = 'МBVС022220212508260023'
OTHER = 'MBVC022220212508260024'
URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v_8176328839'


def saved(tmp_path, description=None):
    text = 'ID: ' + ALIAS if description is None else description
    (tmp_path / 'card.png').write_bytes(b'captured-image')
    manifest = {
        'schema_version': 'evidence.v1',
        'source': 'avito',
        'purpose': 'direct_card',
        'page_number': 1,
        'screenshot_file': 'card.png',
        'screenshot_bytes': 14,
        'screenshot_sha256': hashlib.sha256(b'captured-image').hexdigest(),
        'requested_url_sha256': hashlib.sha256(URL.encode()).hexdigest(),
        'final_url_sha256': hashlib.sha256(URL.encode()).hexdigest(),
        'captured_at': '2026-10-20T13:00:00Z',
        'http_status': 200,
    }
    (tmp_path / 'card.png.json').write_text(json.dumps(manifest))
    return {
        'source': 'avito',
        'url': URL,
        'state': 'active',
        'evidence': 'card.png',
        'card': {
            'placement_id': None,
            'placement_id_raw': ALIAS if text else None,
            'description': text,
            'description_capture': {
                'software_truncated': False,
                'sha256': hashlib.sha256(text.encode()).hexdigest(),
            },
        },
    }


def snapshot(platform_id='8176328839'):
    return PlacementFeedSnapshot(
        {
            'avito-feed-new': [{'Id': UID, 'AvitoId': platform_id, 'Description': '<p>ID: ' + UID + '</p>'}],
            'avito-feed-used': [],
        },
        {},
        {'avito-feed-new': 4, 'avito-feed-used': 4},
    )


def test_alias_is_corroborated_and_raw_claim_and_numeric_suffix_are_preserved(tmp_path):
    c = saved(tmp_path)
    r = resolve_content_identity(c, snapshot(), evidence_dir=tmp_path)
    assert r['unique_id'] == UID and r['basis'] == 'visual_alias' and r['platform_id_verified']
    assert r['raw_claim'] == ALIAS and c['card']['placement_id'] is None
    assert r['unique_id'][4:] == ALIAS[4:]
    r = resolve_content_identity(c, snapshot(''), evidence_dir=tmp_path)
    assert r['unique_id'] == UID and not r['platform_id_verified']  # text + unique feed ID/description


@pytest.mark.parametrize(
    'failure',
    [
        'digit',
        'two_ids',
        'duplicate_feed',
        'wrong_platform',
        'bad_feed_description',
        'bad_capture',
        'bad_image',
        'bad_url',
        'redirect',
    ],
)
def test_alias_does_not_hide_conflicts_or_integrity_failures(tmp_path, failure):
    c = saved(tmp_path)
    s = snapshot()
    if failure == 'digit':
        c = saved(tmp_path, 'ID: ' + ALIAS[:-1] + '4')
    elif failure == 'two_ids':
        c = saved(tmp_path, 'ID: ' + ALIAS + '\nID: ' + OTHER)
    elif failure == 'duplicate_feed':
        s.rows_by_sheet['avito-feed-used'] = list(s.rows_by_sheet['avito-feed-new'])
    elif failure == 'wrong_platform':
        s.rows_by_sheet['avito-feed-new'][0]['AvitoId'] = '9999999999'
    elif failure == 'bad_feed_description':
        s.rows_by_sheet['avito-feed-new'][0]['Description'] = 'ID: ' + OTHER
    elif failure == 'bad_capture':
        c['card']['description_capture']['software_truncated'] = True
    elif failure == 'bad_image':
        (tmp_path / 'card.png').write_bytes(b'tampered-image')
    elif failure == 'bad_url':
        c['url'] = URL + '?changed=true'
    elif failure == 'redirect':
        manifest = json.loads((tmp_path / 'card.png.json').read_text())
        manifest['final_url_sha256'] = hashlib.sha256(b'different ad').hexdigest()
        (tmp_path / 'card.png.json').write_text(json.dumps(manifest))
    r = resolve_content_identity(c, s, evidence_dir=tmp_path)
    assert r['unique_id'] is None and r['status'] != 'resolved'


def test_missing_id_is_not_filled_from_feed_url_without_explicit_prior_confirmation(tmp_path):
    c = saved(tmp_path, '')
    assert resolve_content_identity(c, snapshot(), evidence_dir=tmp_path)['unique_id'] is None
    r = resolve_content_identity(
        c, snapshot(), evidence_dir=tmp_path, operator_confirmed_ids={'avito:8176328839': UID}
    )
    assert r['unique_id'] == UID and r['basis'] == 'operator_confirmed'
    c = saved(tmp_path, 'ID: ' + OTHER)
    assert (
        resolve_content_identity(
            c, snapshot(), evidence_dir=tmp_path, operator_confirmed_ids={'avito:8176328839': UID}
        )['unique_id']
        is None
    )


def test_metadata_exact_id_does_not_disguise_visual_claim_as_exact(tmp_path):
    c = saved(tmp_path)
    c['card']['placement_id'] = UID
    assert resolve_content_identity(c, snapshot(), evidence_dir=tmp_path)['basis'] == 'visual_alias'


@pytest.mark.parametrize('second_id,expected', [(UID, UID), (OTHER, None), ('', None)])
def test_every_duplicate_description_must_independently_corroborate_id(tmp_path, second_id, expected):
    c, s = saved(tmp_path), snapshot()
    s.rows_by_sheet['avito-feed-new'][0]['Description'] = ''
    s.feed_structure['avito-feed-new'] = {'field_values': {'4': {'Description': [
        '<p>Первое описание. ID: ' + UID + '</p>',
        '<p>Другой текст. ' + ('ID: ' + second_id if second_id else 'Нет идентификатора') + '</p>',
    ]}}}
    result = resolve_content_identity(c, s, evidence_dir=tmp_path)
    assert result['unique_id'] == expected
    assert c['card']['placement_id'] is None
