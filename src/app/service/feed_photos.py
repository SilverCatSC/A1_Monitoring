"""Feed-gallery illustrations, separate from vehicle identity and scan coverage.

Photos require an already resolved publication ID. HTTP checks are bounded,
never follow redirects, and never open marketplace pages or wait for CAPTCHA.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.service.company_site_photos import safe_photo_url
from app.service.placement_feed_snapshot import FEED_SPECS
from app.service.placement_identity import parse_placement_id
from app.service.workbook_identity import report_identity_conflicts, workbook_identity_conflicts

MAX_PREFIX = 512 * 1024
CACHE_AGE = timedelta(hours=24)


def safe_feed_photo_url(url):
    try:
        parsed = urlsplit(url)
        return (isinstance(url, str) and parsed.scheme == 'https'
                and not parsed.username and not parsed.password and parsed.port in {None, 443}
                and Path(parsed.path).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}
                and (safe_photo_url(url) or (parsed.hostname == 'storage.yandexcloud.net'
                     and parsed.path.startswith('/a1autofeed/') and '..' not in parsed.path)))
    except (ValueError, TypeError, AttributeError):
        return False


def feed_photo_candidates(review, observed_cards, snapshot):
    """Exact source-specific UID row only; marketing rows and VIN are unnecessary."""
    conflicts = set(workbook_identity_conflicts(snapshot)) | report_identity_conflicts(review)
    saved = {(c.get('source'), c.get('url')): c for c in observed_cards if c.get('state') == 'active'}
    result = []
    for card in review.get('cards', []):
        source, url, uid = card.get('source'), card.get('url'), card.get('unique_id')
        observation = saved.get((source, url))
        if not observation or not uid or uid in conflicts or source not in {'auto_ru', 'avito'}:
            continue
        try:
            if parse_placement_id(uid).value != uid:
                continue
        except ValueError:
            continue
        # Expected feed IDs derived from price, marketing links, or VIN never qualify.
        resolution = card.get('identity_resolution') or {}
        if observation.get('unique_id') != uid and not (
                resolution.get('status') == 'resolved' and resolution.get('unique_id') == uid):
            continue
        sheets = ['autoru-feed-all'] if source == 'auto_ru' else ['avito-feed-new', 'avito-feed-used']
        id_field, image_field = ('unique_id', 'images') if source == 'auto_ru' else ('Id', 'ImageUrls')
        matches = [(sheet, number, row) for sheet in sheets
                   for number, row in enumerate(snapshot.rows_by_sheet.get(sheet, []), snapshot.first_data_rows.get(sheet, 4))
                   if str(row.get(id_field) or '').strip() == uid]
        if len(matches) != 1:
            continue
        sheet, number, row = matches[0]
        if source == 'avito':
            platform_id = str(row.get('AvitoId') or '').strip()
            key = canonical_listing_key(EngineType.AVITO, url)
            owners = [r for sh in sheets for r in snapshot.rows_by_sheet.get(sh, [])
                      if str(r.get('AvitoId') or '').strip() and 'avito:' + str(r['AvitoId']).strip() == key]
            if ((platform_id and ('avito:' + platform_id != key or not platform_id.isdigit()))
                    or len(owners) > 1 or (owners and owners[0].get('Id', '').strip() != uid)):
                continue
        values = snapshot.field_values(sheet, number, image_field, row.get(image_field) or '')
        galleries = [list(dict.fromkeys(re.findall(r'https://[^\s<>"|]+', text or ''))) for text in values]
        if not galleries or any(gallery != galleries[0] for gallery in galleries[1:]):
            continue
        images = [image for image in galleries[0] if safe_feed_photo_url(image)]
        if not images:
            continue
        columns = list(row)
        column = columns.index(image_field) + 1
        locations = snapshot.cell_locations(sheet, number, image_field, column)
        result.append({'source': source, 'url': url, 'unique_id': uid, 'feed_sheet': sheet,
                       'feed_row': number, 'feed_sha256': snapshot.sha256_by_sheet.get(sheet),
                       'gallery_urls': images[:2], 'source_cells': locations,
                       'sheet_url': f'{snapshot.workbook_url}#gid={FEED_SPECS[sheet][0]}',
                       'feed_read_at_utc': snapshot.read_finished_at_utc})
    return result


def raster_dimensions(data):
    """Recognise raster headers and dimensions without an additional image library."""
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24 and data[12:16] == b'IHDR':
        return 'image/png', *struct.unpack('>II', data[16:24])
    if data.startswith(b'\xff\xd8'):
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 255:
                return None
            while pos < len(data) and data[pos] == 255:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]
            pos += 1
            if marker in {0xD8, 0xD9, 0x01, *range(0xD0, 0xD8)}:
                continue
            if marker == 0xDA:
                break
            size = int.from_bytes(data[pos:pos + 2], 'big')
            if size < 2 or pos + size > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2} and size >= 7:
                height, width = struct.unpack('>HH', data[pos + 3:pos + 7])
                return 'image/jpeg', width, height
            pos += size
    if len(data) >= 30 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        if data[12:16] == b'VP8X':
            return 'image/webp', 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
        if data[12:16] == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            return 'image/webp', int.from_bytes(data[26:28], 'little') & 0x3FFF, int.from_bytes(data[28:30], 'little') & 0x3FFF
        if data[12:16] == b'VP8L' and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], 'little')
            return 'image/webp', (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def check_photo_prefix(data, content_type):
    dimensions = raster_dimensions(data)
    declared = content_type.split(';', 1)[0].lower().strip()
    if not dimensions or declared not in {'image/jpeg', 'image/png', 'image/webp'}:
        return None
    mime, width, height = dimensions
    if min(width, height) < 200 or max(width, height) > 20000:
        return None
    return {'mime': mime, 'width': width, 'height': height,
            'prefix_sha256': hashlib.sha256(data).hexdigest(), 'prefix_bytes': len(data),
            **({'reported_mime': declared, 'mime_mismatch': True} if declared != mime else {})}


def _download_check(client, url):
    if not safe_feed_photo_url(url):
        return None, b'', {'reason': 'unsafe_url'}
    started = time.monotonic()
    with client.stream('GET', url, follow_redirects=False, headers={'Range': f'bytes=0-{MAX_PREFIX - 1}'}) as response:
        if response.status_code not in {200, 206}:
            return None, b'', {'reason': 'http_error', 'http_status': response.status_code}
        prefix = bytearray()
        for chunk in response.iter_bytes(16384):
            if time.monotonic() - started > 5:
                return None, b'', {'reason': 'time_budget_exceeded', 'http_status': response.status_code}
            prefix.extend(chunk[:MAX_PREFIX - len(prefix)])
            if len(prefix) >= MAX_PREFIX:
                break
        data = bytes(prefix)
        content_type = response.headers.get('content-type', '')
        check = check_photo_prefix(data, content_type)
        raster = raster_dimensions(data)
        return check, data, {'reason': 'verified' if check else 'invalid_media',
                             'http_status': response.status_code, 'reported_mime': content_type,
                             'detected_mime': raster[0] if raster else None,
                             'prefix_sha256': hashlib.sha256(data).hexdigest(), 'prefix_bytes': len(data)}


def verify_feed_photos(review, evidence_dir, *, client=None):
    """Best-effort media checks; no scan/identity findings or coverage are changed."""
    review['feed_photo_schema_version'] = 1
    candidates = review.get('feed_photo_candidates', [])
    root = Path(evidence_dir)
    cache_path = root / 'feed_photo_checks.json'
    samples = root / 'feed_photo_samples'
    now = datetime.now(UTC)
    cache = {}
    try:
        if not cache_path.is_symlink() and cache_path.stat().st_size < 2 * 1024 * 1024:
            cache = json.loads(cache_path.read_text())
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        pass
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(3, connect=2), follow_redirects=False)

    def verify(url):
        cached = cache.get(url)
        try:
            if (cached and re.fullmatch(r'[a-f0-9]{64}', cached['prefix_sha256'])
                    and timedelta(0) <= now - datetime.fromisoformat(cached['checked_at_utc']) < CACHE_AGE):
                path = samples / (cached['prefix_sha256'] + '.bin')
                if (not path.is_symlink() and path.stat().st_size <= MAX_PREFIX
                        and check_photo_prefix(path.read_bytes(), cached['mime']) ==
                        {k: cached[k] for k in ('mime', 'width', 'height', 'prefix_sha256', 'prefix_bytes')}):
                    return url, cached, {'reason': 'verified_cache'}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            check, data, diagnostic = _download_check(client, url)
            if check:
                samples.mkdir(parents=True, exist_ok=True)
                target = samples / (check['prefix_sha256'] + '.bin')
                if target.is_symlink():
                    return url, None, {'reason': 'invalid_sample_path'}
                target.write_bytes(data)
                return url, {**check, 'checked_at_utc': now.isoformat()}, diagnostic
            if data:
                failed = root / 'feed_photo_failures'
                failed.mkdir(parents=True, exist_ok=True)
                target = failed / (hashlib.sha256(data).hexdigest() + '.bin')
                if not target.is_symlink():
                    target.write_bytes(data)
            return url, None, diagnostic
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return url, None, {'reason': 'request_or_storage_error', 'error_type': type(exc).__name__}

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            urls = list(dict.fromkeys(c['gallery_urls'][0] for c in candidates))
            outcomes = list(pool.map(verify, urls))
            checks = {url: check for url, check, _ in outcomes}
            fallback = list(dict.fromkeys(c['gallery_urls'][1] for c in candidates
                                         if not checks.get(c['gallery_urls'][0]) and len(c['gallery_urls']) > 1))
            outcomes += list(pool.map(verify, [url for url in fallback if url not in checks]))
            checks = {url: check for url, check, _ in outcomes}
    finally:
        if own_client:
            client.close()
    diagnostics = {url: diagnostic for url, _, diagnostic in outcomes}
    review['feed_photos'], review['feed_photo_checks'] = [], []
    for candidate in candidates:
        image = next((url for url in candidate['gallery_urls'] if checks.get(url)), None)
        review['feed_photo_checks'].append({k: candidate[k] for k in ('source', 'url', 'unique_id')}
                                          | {'status': 'verified' if image else 'image_unavailable_or_invalid',
                                             'attempts': [{'image_url': url, **diagnostics[url]}
                                                          for url in candidate['gallery_urls'] if url in diagnostics]})
        if image:
            review['feed_photos'].append({**candidate, 'image_url': image, 'verification': checks[image],
                                         'status': 'verified', 'captured_at_utc': candidate['feed_read_at_utc']})
    try:
        cache.update({url: check for url, check in checks.items() if check})
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='feed_photo_checks_', suffix='.tmp', delete=False) as output:
            json.dump(cache, output, ensure_ascii=False)
            temporary = Path(output.name)
        temporary.replace(cache_path)
    except OSError:
        pass  # Optional illustrations must never fail an otherwise valid report.
    return review['feed_photos']
