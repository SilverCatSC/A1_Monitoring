import copy
import json

import pytest

from app.service.company_site_photos import (
    apply_company_photo,
    parse_company_photos,
    read_company_photo_index,
    safe_photo_url,
)

VIN = 'W1VVNLTZ4T4788708'
PAGE = 'https://a1auto.ru/cars-for-sale/comfort-guard.html'
PHOTO = 'https://a1auto.ru/upload/iblock/abc/car.jpg'


def index():
    return {'schema_version': 1, 'sheet': 'Размещение_автомобилей', 'entries': [{
        'marketing_vin': VIN, 'page_url': PAGE, 'image_url': PHOTO, 'site_vins': [VIN],
        'captured_at_utc': '2026-10-28T18:00:00+00:00',
    }]}


def test_gallery_excludes_logos_tracking_and_other_hosts():
    parsed = parse_company_photos(f'''
        <img src="/upload/logo.jpg"><main><div class="carousel">
        <img src="/upload/placeholder.png" data-src="/upload/iblock/abc/car.jpg">
        <img src="https://evil.example/track.jpg"><img src="javascript:evil()">
        </div><table class="structured-table"><tr><td>VIN</td><td>{VIN}</td></tr></table></main>
        <footer><img src="/upload/other-car.jpg"></footer>''', PAGE)
    assert parsed == {'images': [PHOTO], 'vins': [VIN]}


@pytest.mark.parametrize('url', ['https://a1auto.ru.evil.test/upload/car.jpg',
                               'https://user:pass@a1auto.ru/upload/car.jpg',
                               'https://a1auto.ru:bad/upload/car.jpg',
                               'https://a1auto.ru/upload/script.svg', 'http://a1auto.ru/upload/car.jpg'])
def test_untrusted_image_urls_are_rejected(url):
    assert not safe_photo_url(url)


def test_workbook_photo_does_not_assign_unique_id_or_change_platform_state():
    tile = {'marketing_row': 11, 'vin': VIN, 'unique_id': None,
            'platforms': [{'source': 'avito', 'state': 'unknown'}], 'image': 'feed.jpg'}
    before = copy.deepcopy(tile)
    apply_company_photo(tile, index())
    assert tile['image'] == PHOTO
    assert tile['unique_id'] is None
    assert tile['platforms'] == before['platforms']
    assert tile['image_source_url'] == PAGE


@pytest.mark.parametrize('case', ['unbound_advert', 'wrong_vin', 'site_mismatch', 'duplicate_row', 'missing_site_vin'])
def test_uncertain_photo_mapping_preserves_existing_image(case):
    tile = {'marketing_row': 11, 'vin': VIN, 'image': 'feed.jpg'}
    data = index()
    if case == 'unbound_advert':
        tile.pop('marketing_row')
    elif case == 'wrong_vin':
        tile['vin'] = 'W1VVNLTZ8T4638988'
    elif case == 'site_mismatch':
        data['entries'][0]['site_vins'] = ['W1VVNLTZ8T4638988']
    elif case == 'duplicate_row':
        data['entries'] *= 2
    else:
        data['entries'][0]['site_vins'] = None
    apply_company_photo(tile, data)
    assert tile['image'] == 'feed.jpg'


def test_missing_and_invalid_photo_index_fail_closed(tmp_path):
    assert read_company_photo_index(tmp_path) == {}
    p = tmp_path / 'company_site_photos.json'
    p.write_text(json.dumps(index()))
    assert read_company_photo_index(tmp_path)['entries']
    p.unlink()
    target = tmp_path / 'other.json'
    target.write_text(json.dumps(index()))
    p.symlink_to(target)
    assert read_company_photo_index(tmp_path) == {}
