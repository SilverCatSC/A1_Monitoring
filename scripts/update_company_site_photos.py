"""Read only the website pages explicitly linked by a saved marketing-sheet read."""

import argparse
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.service.company_site_photos import parse_company_photos, safe_page_url
from app.service.vehicle_content import vin_values


def collect_photos(links, output):
    captured = datetime.now(UTC).isoformat()
    rows = [r for r in links['rows'] if r.get('status') == 'Актуально']
    vins = Counter(tuple(sorted(vin_values(r.get('vin')))) for r in rows)
    site_links = Counter(r.get('site_url') for r in rows if safe_page_url(r.get('site_url')))
    pages_dir = output.parent / 'company_site_photo_pages'
    pages_dir.mkdir(parents=True, exist_ok=True)

    def visit(row):
        base = {'marketing_row': row['row'], 'marketing_vin': row.get('vin'),
                'title': row.get('title'), 'page_url': row.get('site_url'),
                'sheet_cell': f"Z{row['row']}", 'captured_at_utc': captured}
        expected = vin_values(row.get('vin'))
        if not safe_page_url(row.get('site_url')):
            return {**base, 'result': 'no_valid_website_link'}
        if not expected or vins[tuple(sorted(expected))] != 1 or site_links[row['site_url']] != 1:
            return {**base, 'result': 'ambiguous_workbook_row'}
        try:
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                url = row['site_url']
                for _ in range(4):
                    response = client.get(url)
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get('location', ''))
                        if not safe_page_url(url):
                            raise ValueError('off-site redirect')
                        continue
                    response.raise_for_status()
                    break
                else:
                    raise ValueError('redirect limit')
                if len(response.content) > 4 * 1024 * 1024:
                    raise ValueError('page too large')
                page_hash = hashlib.sha256(response.content).hexdigest()
                (pages_dir / f'{page_hash}.html').write_bytes(response.content)
                parsed = parse_company_photos(response.text, url)
                if not parsed['vins']:
                    return {**base, 'result': 'website_vin_missing', 'site_vins': []}
                if not set(parsed['vins']).issubset(expected):
                    return {**base, 'result': 'website_vin_mismatch', 'site_vins': parsed['vins']}
                if not parsed['images']:
                    return {**base, 'result': 'no_gallery_photos'}
                # Read headers only; pictures stay on the company's website.
                with client.stream('GET', parsed['images'][0]) as image:
                    image.raise_for_status()
                    if not image.headers.get('content-type', '').startswith('image/'):
                        raise ValueError('not an image')
                return {**base, 'page_url': url, 'result': 'matched', 'image_url': parsed['images'][0],
                        'gallery_urls': parsed['images'], 'site_vins': parsed['vins'], 'page_sha256': page_hash}
        except (httpx.HTTPError, ValueError):
            return {**base, 'result': 'website_unavailable'}

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(visit, rows))
    index = {'schema_version': 1, 'captured_at_utc': captured,
             'workbook_url': links['spreadsheet_url'], 'sheet': links['sheet'],
             'entries': [r for r in results if r['result'] == 'matched'], 'checks': results}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps(index, ensure_ascii=False, indent=2))
    temp.replace(output)
    return dict(Counter(r['result'] for r in results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--links', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(collect_photos(json.loads(args.links.read_text()), args.output), ensure_ascii=False))
