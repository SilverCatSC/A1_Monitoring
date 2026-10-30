import copy
import json
import struct
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.service.feed_photos import (
    MAX_PREFIX,
    check_photo_prefix,
    feed_photo_candidates,
    raster_dimensions,
    safe_feed_photo_url,
    verify_feed_photos,
)
from app.service.placement_feed_snapshot import PlacementFeedSnapshot
from app.service.vehicle_dashboard import vehicle_dashboard

UID = 'MBVC022220202508260021'
URL = 'https://www.avito.ru/moskva/avtomobili/car_7888035437'
IMAGE = 'https://storage.yandexcloud.net/a1autofeed/car/1.jpg'
PNG_IMAGE = 'https://storage.yandexcloud.net/a1autofeed/car/1.png'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x0dIHDR' + struct.pack('>II', 800, 600) + b'\x00' * 100


def fixture(source='avito'):
    sheet = 'autoru-feed-all' if source == 'auto_ru' else 'avito-feed-used'
    row = {'unique_id': UID, 'images': PNG_IMAGE} if source == 'auto_ru' else {
        'Id': UID, 'AvitoId': '7888035437', 'ImageUrls': PNG_IMAGE}
    url = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1103613360-6d889424/' if source == 'auto_ru' else URL
    snapshot = PlacementFeedSnapshot(rows_by_sheet={sheet: [row]}, sha256_by_sheet={sheet: 'feedhash'},
                                     first_data_rows={sheet: 4}, marketing_rows=[],
                                     workbook_url='https://docs.google.com/spreadsheets/d/test/edit',
                                     read_finished_at_utc='2026-10-30T12:00:00+00:00')
    observed = [{'source': source, 'url': url, 'state': 'active', 'unique_id': UID, 'card': {'price': 5600000}}]
    review = {'cards': [{'source': source, 'url': url, 'unique_id': UID,
                        'marketing_binding': {'status': 'marketing_row_missing'}}]}
    return review, observed, snapshot


@pytest.mark.parametrize('source', ['avito', 'auto_ru'])
def test_exact_published_id_extracts_photo_without_marketing_row_or_vin(source):
    review, observed, snapshot = fixture(source)
    before = copy.deepcopy((review, observed, snapshot))
    candidates = feed_photo_candidates(review, observed, snapshot)
    assert len(candidates) == 1
    assert candidates[0]['unique_id'] == UID and candidates[0]['gallery_urls'] == [PNG_IMAGE]
    assert candidates[0]['feed_sha256'] == 'feedhash' and candidates[0]['feed_row'] == 4
    assert (review, observed, snapshot) == before


@pytest.mark.parametrize('problem', ['missing_id', 'expected_id_only', 'duplicate_feed', 'other_source_only',
                                     'platform_mismatch', 'platform_shared', 'conflict', 'ambiguous_gallery'])
def test_no_photo_identity_inference_or_ambiguous_feed_choice(problem):
    review, observed, snapshot = fixture()
    row = snapshot.rows_by_sheet['avito-feed-used'][0]
    if problem == 'missing_id':
        review['cards'][0]['unique_id'] = None
    elif problem == 'expected_id_only':
        observed[0]['unique_id'] = None
        review['cards'][0]['expected_workbook_ids'] = [UID]
    elif problem == 'duplicate_feed':
        snapshot.rows_by_sheet['avito-feed-used'].append(dict(row))
    elif problem == 'other_source_only':
        snapshot.rows_by_sheet.clear()
        snapshot.rows_by_sheet['autoru-feed-all'] = [{'unique_id': UID, 'images': PNG_IMAGE}]
    elif problem == 'platform_mismatch':
        row['AvitoId'] = '9999999999'
    elif problem == 'platform_shared':
        snapshot.rows_by_sheet['avito-feed-used'].append({**row, 'Id': 'MBVC022220202508260022'})
    elif problem == 'conflict':
        review['identity_conflicts'] = {UID: {}}
    else:
        snapshot.feed_structure['avito-feed-used'] = {'field_values': {'4': {'ImageUrls': [PNG_IMAGE, IMAGE]}}}
    assert feed_photo_candidates(review, observed, snapshot) == []


@pytest.mark.parametrize('url', ['http://storage.yandexcloud.net/a1autofeed/car.jpg',
    'https://127.0.0.1/a1autofeed/car.jpg', 'https://storage.yandexcloud.net/other/car.jpg',
    'https://storage.yandexcloud.net.evil.test/a1autofeed/car.jpg',
    'https://user:pass@storage.yandexcloud.net/a1autofeed/car.jpg',
    'https://storage.yandexcloud.net:444/a1autofeed/car.jpg',
    'https://storage.yandexcloud.net/a1autofeed/file.svg', 'javascript:alert(1)', None])
def test_reject_unsafe_image_sources(url):
    assert not safe_feed_photo_url(url)


def test_raster_signatures_mime_and_dimensions():
    assert raster_dimensions(PNG) == ('image/png', 800, 600)
    assert check_photo_prefix(PNG, 'image/png; charset=binary')['width'] == 800
    assert not check_photo_prefix(PNG, 'text/html')
    assert not check_photo_prefix(b'<html>blocked</html>', 'image/png')
    assert not check_photo_prefix(PNG[:16] + struct.pack('>II', 1, 1), 'image/png')
    jpeg = b'\xff\xd8\xff\xc0\x00\x08\x08' + struct.pack('>HH', 600, 800) + b'\x01'
    assert raster_dimensions(jpeg) == ('image/jpeg', 800, 600)


def test_success_persists_proof_and_renders_unbound_card_without_changing_status(tmp_path):
    review, observed, snapshot = fixture()
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed, snapshot)
    original_cards = copy.deepcopy(observed)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={'content-type': 'image/png'}, content=PNG)))
    photos = verify_feed_photos(review, tmp_path, client=client)
    assert len(photos) == 1 and photos[0]['verification']['width'] == 800
    report = {'cycle_id': 'cycle', 'automatic_content_checks': review, 'observed_cards': observed}
    tile = vehicle_dashboard(report)['vehicles'][0]
    assert tile['image'] == PNG_IMAGE and not tile.get('marketing_row') and tile['vin'] == ''
    assert tile['platforms'][0]['state'] == 'active' and observed == original_cards
    proof = photos[0]['verification']
    assert (tmp_path / 'feed_photo_samples' / (proof['prefix_sha256'] + '.bin')).is_file()
    def no_network(request):
        raise AssertionError('fresh intact media cache should avoid another request')
    verify_feed_photos(review, tmp_path, client=httpx.Client(transport=httpx.MockTransport(no_network)))
    assert review['feed_photos'][0]['image_url'] == PNG_IMAGE


@pytest.mark.parametrize('status,body,mime', [(429,b'blocked','text/html'), (302,PNG,'image/png'),
    (200,b'<html>access error</html>','image/png'), (200,PNG,'text/html')])
def test_bad_media_never_throws_or_changes_card_identity(tmp_path, status, body, mime):
    review, observed, snapshot = fixture()
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed, snapshot)
    before = copy.deepcopy(observed)
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={'content-type': mime, 'location': 'https://evil.test/x'}, content=body)
    assert verify_feed_photos(review, tmp_path, client=httpx.Client(transport=httpx.MockTransport(response))) == []
    assert calls == [PNG_IMAGE] and observed == before
    assert review['feed_photo_checks'][0]['status'] == 'image_unavailable_or_invalid'
    assert not vehicle_dashboard({'cycle_id': 'c', 'automatic_content_checks': review,
                                  'observed_cards': observed})['vehicles'][0]['image']


def test_failed_first_photo_uses_next_gallery_photo_and_deduplicates_requests(tmp_path):
    review, observed, snapshot = fixture()
    snapshot.rows_by_sheet['avito-feed-used'][0]['ImageUrls'] = IMAGE + ' | ' + PNG_IMAGE
    candidates = feed_photo_candidates(review, observed, snapshot)
    review['feed_photo_candidates'] = candidates + copy.deepcopy(candidates)
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(404 if str(request.url) == IMAGE else 200,
                              headers={'content-type': 'image/png'}, content=PNG)
    photos = verify_feed_photos(review, tmp_path, client=httpx.Client(transport=httpx.MockTransport(response)))
    assert len(photos) == 2 and all(p['image_url'] == PNG_IMAGE for p in photos)
    assert calls == [IMAGE, PNG_IMAGE]


@pytest.mark.parametrize('change', ['tampered', 'expired'])
def test_bad_or_old_cache_is_rechecked(tmp_path, change):
    review, observed, snapshot = fixture()
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed, snapshot)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={'content-type': 'image/png'}, content=PNG)))
    photo = verify_feed_photos(review, tmp_path, client=client)[0]
    if change == 'tampered':
        (tmp_path / 'feed_photo_samples' / (photo['verification']['prefix_sha256'] + '.bin')).write_bytes(b'bad')
    else:
        cache = json.loads((tmp_path / 'feed_photo_checks.json').read_text())
        cache[PNG_IMAGE]['checked_at_utc'] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        (tmp_path / 'feed_photo_checks.json').write_text(json.dumps(cache))
    failed = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    assert verify_feed_photos(review, tmp_path, client=failed) == []


def test_download_is_bounded(tmp_path):
    review, observed, snapshot = fixture()
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed, snapshot)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={'content-type': 'image/png'}, content=PNG + b'0' * MAX_PREFIX * 2)))
    photo = verify_feed_photos(review, tmp_path, client=client)[0]
    assert photo['verification']['prefix_bytes'] == MAX_PREFIX


def test_checked_reports_do_not_restore_unchecked_marketing_feed_image():
    review, observed, snapshot = fixture()
    review.update(feed_photo_schema_version=1, feed_photos=[], marketing_vehicles=[{
        'key': 'marketing:2', 'unique_id': UID, 'marketing_row': 2, 'title': 'Car',
        'image': PNG_IMAGE, 'expected_urls': {'avito': URL}}])
    tile = vehicle_dashboard({'cycle_id': 'c', 'automatic_content_checks': review,
                              'observed_cards': observed})['vehicles'][0]
    assert tile['image'] is None


def test_resolved_identity_alias_can_supply_photos_but_expected_ids_cannot():
    review, observed, snapshot = fixture()
    observed[0]['unique_id'] = None
    review['cards'][0]['identity_resolution'] = {'status': 'resolved', 'unique_id': UID}
    assert len(feed_photo_candidates(review, observed, snapshot)) == 1
    review['cards'][0]['identity_resolution']['status'] = 'evidence_unverified'
    assert not feed_photo_candidates(review, observed, snapshot)


@pytest.mark.parametrize('kind', ['VP8X', 'VP8 ', 'VP8L'])
def test_webp_raster_dimensions(kind):
    prefix = b'RIFF' + b'\x00' * 4 + b'WEBP' + kind.encode() + b'\x00' * 4
    if kind == 'VP8X':
        data = prefix + b'\x00' * 4 + (799).to_bytes(3, 'little') + (599).to_bytes(3, 'little')
    elif kind == 'VP8 ':
        data = prefix + b'\x00' * 3 + b'\x9d\x01\x2a' + struct.pack('<HH', 800, 600)
    else:
        data = prefix + b'\x2f' + (799 | (599 << 14)).to_bytes(4, 'little') + b'\x00' * 5
    assert raster_dimensions(data) == ('image/webp', 800, 600)


def test_png_mislabeled_as_jpeg_keeps_signature_and_records_warning(tmp_path):
    review, observed, snapshot = fixture()
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed, snapshot)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        206, headers={'content-type': 'image/jpeg'}, content=PNG)))
    photo = verify_feed_photos(review, tmp_path, client=client)[0]
    assert photo['verification']['mime'] == 'image/png'
    assert photo['verification']['reported_mime'] == 'image/jpeg'
    assert photo['verification']['mime_mismatch'] is True
    assert review['feed_photo_checks'][0]['attempts'][0]['detected_mime'] == 'image/png'
    def no_request(request):
        raise AssertionError('a mislabeled image with intact cached proof must be reused')
    assert verify_feed_photos(review, tmp_path, client=httpx.Client(transport=httpx.MockTransport(no_request)))


@pytest.mark.parametrize('mime', ['text/html', 'image/svg+xml', 'application/octet-stream', ''])
def test_correct_raster_prefix_never_overrides_non_raster_content_type(mime):
    assert check_photo_prefix(PNG, mime) is None


def test_failed_check_retains_reason_status_and_bounded_sample(tmp_path):
    review, observed, snapshot = fixture()
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed, snapshot)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={'content-type': 'image/jpeg'}, content=b'<html>Access denied</html>')))
    assert not verify_feed_photos(review, tmp_path, client=client)
    attempt = review['feed_photo_checks'][0]['attempts'][0]
    assert attempt['reason'] == 'invalid_media' and attempt['http_status'] == 200
    assert attempt['detected_mime'] is None
    assert (tmp_path / 'feed_photo_failures' / (attempt['prefix_sha256'] + '.bin')).read_bytes().startswith(b'<html>')
