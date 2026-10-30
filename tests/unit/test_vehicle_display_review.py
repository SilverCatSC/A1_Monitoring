import hashlib
import json

from app.service.vehicle_display_review import load_display_bindings


def test_display_review_requires_intact_snapshot_and_provenance(tmp_path):
    cycle = '6268d930-61c2-4d82-9107-d4cdab53758a'
    directory = tmp_path / 'cycles' / cycle
    directory.mkdir(parents=True)
    original = directory / 'placement_reconciliation.json'
    original.write_text('{}')
    binding = {'source': 'auto_ru', 'url': 'https://auto.ru/lcv/used/sale/mercedes/sprinter/1126771072-2b6f5948/',
               'unique_id': 'MBSP022320242209260038', 'basis': 'Owner reviewed exact cards',
               'confirmed_by': 'owner', 'recorded_on': '2026-10-30'}
    payload = {'schema_version': 1, 'cycle_id': cycle,
               'report_sha256': hashlib.sha256(original.read_bytes()).hexdigest(), 'bindings': [binding]}
    path = directory / 'vehicle_display_review.json'
    path.write_text(json.dumps(payload))
    assert load_display_bindings(tmp_path, {'cycle_id': cycle}) == [binding]
    original.write_text('{"changed": true}')
    assert load_display_bindings(tmp_path, {'cycle_id': cycle}) == []
    assert load_display_bindings(tmp_path, {'cycle_id': '../escape'}) == []


def test_feed_photo_requires_snapshot_hash_exact_id_platform_id_and_gallery(tmp_path):
    from app.service.vehicle_display_review import load_display_photos
    cycle = '6268d930-61c2-4d82-9107-d4cdab53758a'
    directory = tmp_path / 'cycles' / cycle
    directory.mkdir(parents=True)
    original = directory / 'placement_reconciliation.json'
    original.write_text('{}')
    uid = 'MBVC022220202508260021'
    url = 'https://www.avito.ru/moskva/avtomobili/car_7888035437'
    image = 'https://storage.yandexcloud.net/a1autofeed/car.jpg'
    snapshot = directory / 'feed.json'
    snapshot.write_text(json.dumps({'rows_by_sheet': {'avito-feed-used': [
        {'Id': uid, 'AvitoId': '7888035437', 'ImageUrls': image}]}}))
    photo = {'source': 'avito', 'url': url, 'unique_id': uid, 'image_url': image,
             'feed_sheet': 'avito-feed-used', 'snapshot_file': 'feed.json',
             'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}
    path = directory / 'vehicle_display_review.json'
    def write():
        path.write_text(json.dumps({'schema_version': 1, 'cycle_id': cycle, 'bindings': [],
                                   'report_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                                   'photos': [photo]}))
    write()
    report = {'cycle_id': cycle, 'observed_cards': [{'state': 'active', 'source': 'avito',
              'url': url, 'unique_id': uid}]}
    assert load_display_photos(tmp_path, report) == [photo]
    for key, value in [('image_url', 'https://storage.yandexcloud.net/a1autofeed/other.jpg'),
                       ('unique_id', 'OTHER'), ('url', url + '99'),
                       ('snapshot_file', '../feed.json'), ('snapshot_sha256', 'tampered')]:
        old = photo[key]
        photo[key] = value
        write()
        assert load_display_photos(tmp_path, report) == []
        photo[key] = old
    write()
    snapshot.write_text('{}')
    assert load_display_photos(tmp_path, report) == []


def test_malformed_photo_registry_is_ignored(tmp_path):
    from app.service.vehicle_display_review import load_display_photos
    cycle = '6268d930-61c2-4d82-9107-d4cdab53758a'
    directory = tmp_path / 'cycles' / cycle
    directory.mkdir(parents=True)
    raw = b'{}'
    (directory / 'placement_reconciliation.json').write_bytes(raw)
    (directory / 'vehicle_display_review.json').write_text(json.dumps({
        'schema_version': 1, 'cycle_id': cycle, 'bindings': [], 'photos': None,
        'report_sha256': hashlib.sha256(raw).hexdigest()}))
    assert load_display_photos(tmp_path, {'cycle_id': cycle}) == []
