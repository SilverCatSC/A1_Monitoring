import copy
import json
import struct
from datetime import UTC, datetime

import pytest

from app.service.feed_photo_review import apply_feed_photo_review, save_feed_photo_review
from app.service.feed_photos import check_photo_prefix

CID = 'saved-cycle'
UID = 'MBVC022220202508260021'
URL = 'https://www.avito.ru/moskva/avtomobili/car_7888035437'
IMAGE = 'https://storage.yandexcloud.net/a1autofeed/car/1.jpg'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x0dIHDR' + struct.pack('>II', 800, 600) + b'\x00' * 100


def fixture(root):
    key = {'source': 'avito', 'url': URL, 'unique_id': UID}
    candidate = {**key, 'gallery_urls': [IMAGE], 'feed_read_at_utc': '2026-10-30T12:00:00+00:00'}
    report = {'cycle_id': CID, 'observed_cards': [{**key, 'state': 'active', 'card': {'price': 5600000}}],
              'analytical_summary': {'counts': {'issues': 35}},
              'automatic_content_checks': {'feed_photo_candidates': [candidate], 'feed_photos': [],
                                           'feed_photo_checks': [{**key, 'status': 'image_unavailable_or_invalid'}]}}
    path = root / 'cycles' / CID / 'placement_reconciliation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report))
    check = {**check_photo_prefix(PNG, 'image/jpeg'), 'checked_at_utc': datetime.now(UTC).isoformat()}
    samples = root / 'feed_photo_samples'
    samples.mkdir()
    sample = samples / (check['prefix_sha256'] + '.bin')
    sample.write_bytes(PNG)
    return path, report, {**candidate, 'image_url': IMAGE, 'verification': check}, sample


def test_saved_photo_repair_preserves_observations_business_findings_and_original(tmp_path):
    path, report, entry, sample = fixture(tmp_path)
    raw = path.read_bytes()
    before = copy.deepcopy(report)
    save_feed_photo_review(path, tmp_path, [entry])
    assert apply_feed_photo_review(report, tmp_path, original_raw=raw)
    assert report['observed_cards'] == before['observed_cards']
    assert report['analytical_summary'] == before['analytical_summary']
    assert path.read_bytes() == raw
    assert report['automatic_content_checks']['feed_photo_checks'][0]['status'] == 'verified'
    assert report['feed_photo_review_info']['mime_label_warnings'] == 1
    assert apply_feed_photo_review(report, tmp_path, original_raw=raw)  # Idempotent readback.


@pytest.mark.parametrize('change', ['different_url', 'different_uid', 'duplicate', 'missing_sample', 'tampered_sample'])
def test_photo_review_refuses_unproven_associations(tmp_path, change):
    path, report, entry, sample = fixture(tmp_path)
    entries = [entry]
    if change == 'different_url':
        entry['image_url'] += '?different=1'
    elif change == 'different_uid':
        entry['unique_id'] = 'OTHER'
    elif change == 'duplicate':
        entries.append(copy.deepcopy(entry))
    elif change == 'missing_sample':
        sample.unlink()
    else:
        sample.write_bytes(b'tampered')
    with pytest.raises((ValueError, OSError)):
        save_feed_photo_review(path, tmp_path, entries)
    assert not (path.parent / 'feed_photo_review.ready.json').exists()


def test_photo_review_refuses_changed_original_or_bundle(tmp_path):
    path, report, entry, sample = fixture(tmp_path)
    save_feed_photo_review(path, tmp_path, [entry])
    with pytest.raises(ValueError, match='scope'):
        apply_feed_photo_review(report, tmp_path, original_raw=b'{"other":true}')
    review_path = path.parent / 'feed_photo_review.json'
    review_path.write_text('{}')
    with pytest.raises(ValueError):
        apply_feed_photo_review(report, tmp_path, original_raw=path.read_bytes())
