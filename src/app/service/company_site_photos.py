"""Illustrations from explicit workbook links, never advert identity evidence."""

import json
import stat
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.scraper.base import is_company_site_listing_url
from app.service.vehicle_content import vin_values

INDEX_NAME = 'company_site_photos.json'


def safe_photo_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in {'a1auto.ru', 'www.a1auto.ru'}
                and not parsed.username and not parsed.password and parsed.port in {None, 443}
                and parsed.path.startswith('/upload/')
                and Path(parsed.path).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'})
    except ValueError:
        return False


def safe_page_url(url):
    try:
        parsed = urlsplit(str(url or ''))
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in {None, 443} and is_company_site_listing_url(url))
    except ValueError:
        return False


def parse_company_photos(html, page_url):
    if not safe_page_url(page_url):
        return {'images': [], 'vins': []}
    soup = BeautifulSoup(html, 'html.parser')
    images = []
    for image in soup.select('main .carousel img'):
        url = urljoin(page_url, str(image.get('data-src') or image.get('src') or ''))
        if safe_photo_url(url) and url not in images:
            images.append(url)
    vins = set()
    for row in soup.select('table.structured-table tr'):
        cells = row.select('td')
        if len(cells) >= 2 and cells[0].get_text(strip=True).casefold() == 'vin':
            vins.update(vin_values(cells[1].get_text(' ', strip=True)))
    return {'images': images, 'vins': sorted(vins)}


def read_company_photo_index(evidence_dir):
    path = Path(evidence_dir) / INDEX_NAME
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text())
        if (data.get('schema_version') == 1 and data.get('sheet') == 'Размещение_автомобилей'
                and isinstance(data.get('entries'), list)):
            return data
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}


def apply_company_photo(tile, index):
    # Join only marketing-table rows. Unbound marketplace cards never gain a
    # photo/identity through VIN, price, model, or a neighbouring workbook row.
    if not tile.get('marketing_row'):
        return
    vins = vin_values(tile.get('vin'))
    if not vins:
        return
    matches = [e for e in index.get('entries', []) if isinstance(e, dict)
               and vin_values(e.get('marketing_vin')) == vins]
    if len(matches) != 1:
        return
    entry = matches[0]
    site_vins = entry.get('site_vins')
    if (not safe_page_url(entry.get('page_url'))
            or not safe_photo_url(entry.get('image_url'))
            or not isinstance(site_vins, list) or not site_vins
            or not all(isinstance(v, str) for v in site_vins)
            or not set(site_vins).issubset(vins)):
        return
    tile['image'] = entry['image_url']
    tile['image_source'] = 'Фото с A1auto.ru'
    tile['image_source_url'] = entry['page_url']
    tile['image_captured_at'] = entry.get('captured_at_utc')
