"""Read-only reconciliation with the public A1Auto vehicle catalogue.

The marketing table provides the expected roster, dealer cabinets provide the
marketplace roster, and this module provides a third, independent website
roster.  It never updates the mirror, links, or statuses automatically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.importer.sheet_csv import CsvOrXlsxReader
from app.scraper.base import canonical_company_site_key, is_company_site_listing_url
from app.service.head_table_audit import _head_row


def _text(value: Any) -> str | None:
    result = ' '.join(str(value or '').split())
    return result or None


def _money(value: str) -> float | None:
    match = re.search(r'(?<![0-9A-Za-zА-Яа-я])(\d[\d\s\xa0]{4,})\s*₽', value)
    if not match:
        return None
    digits = re.sub(r'\D', '', match.group(1))
    return float(digits) if digits else None


def _publication_state(value: str) -> str:
    text = value.casefold()
    if 'продано' in text:
        return 'sold'
    if any(marker in text for marker in ('в наличии', 'в производстве', 'в пути')):
        return 'published'
    return 'unknown'


def parse_company_site_catalog(html: str, catalog_url: str) -> list[dict[str, Any]]:
    """Extract vehicle-card links only; no fuzzy identity is inferred here."""
    candidates: dict[str, dict[str, Any]] = {}
    soup = BeautifulSoup(html, 'html.parser')
    for anchor in soup.select('a[href]'):
        url = urljoin(catalog_url, str(anchor.get('href') or ''))
        key = canonical_company_site_key(url)
        if not key:
            continue
        text = _text(anchor.get_text(' ', strip=True))
        if not text:
            continue
        candidate = {
            'site_key': key,
            'url': url.split('#', 1)[0],
            'title': text[:500],
            'price': _money(text),
            'publication_state': _publication_state(text),
        }
        previous = candidates.get(key)
        if previous is None or len(candidate['title']) > len(previous['title']):
            candidates[key] = candidate
    return sorted(candidates.values(), key=lambda item: item['site_key'])


def fetch_company_site_catalog(url: str) -> dict[str, Any]:
    """Fetch only the configured public catalogue; redirects off a1auto.ru are rejected."""
    with httpx.Client(timeout=settings.request_timeout_seconds, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    final_key = canonical_company_site_key(urljoin(str(response.url), '/cars-for-sale/'))
    if not final_key:
        # A catalogue itself deliberately has no listing key. Verify the final host/path instead.
        final = str(response.url).rstrip('/')
        expected = settings.company_site_catalog_url.rstrip('/')
        if final != expected:
            raise RuntimeError('Company site catalogue redirected away from the configured A1Auto page')
    return {
        'requested_url': url,
        'final_url': str(response.url),
        'http_status': response.status_code,
        'candidates': parse_company_site_catalog(response.text, str(response.url)),
    }


def _same_price(left: float | None, right: float | None) -> bool | None:
    if left is None or right is None:
        return None
    return round(left) == round(right)


def _active_marketing_records(
    reader_factory: Callable[[str], Any] = CsvOrXlsxReader,
) -> list[dict[str, Any]]:
    if not settings.head_table_google_sheet_export_url:
        raise RuntimeError('HEAD_TABLE_GOOGLE_SHEET_EXPORT_URL is not configured')
    records = []
    for row in reader_factory(settings.head_table_google_sheet_export_url).read():
        record = _head_row(row.row_number, row.source)
        if record and record['active']:
            records.append(record)
    return records


def audit_company_site(
    *,
    fetcher: Callable[[str], dict[str, Any]] = fetch_company_site_catalog,
    reader_factory: Callable[[str], Any] = CsvOrXlsxReader,
) -> dict[str, Any]:
    """Compare active marketing-table records with the live A1Auto catalogue."""
    source = fetcher(settings.company_site_catalog_url)
    candidates = source['candidates']
    by_key = {candidate['site_key']: candidate for candidate in candidates}
    issues: list[dict[str, Any]] = []
    matched_keys: set[str] = set()
    active = _active_marketing_records(reader_factory)

    for record in active:
        direct_url = record['site_url']
        key = canonical_company_site_key(direct_url)
        base = {
            'vehicle_key': record['vehicle_key'],
            'vin': record['vin'],
            'head_row': record['row_number'],
            'marketing_status': record['status'],
            'direct_url': direct_url,
        }
        if not direct_url:
            issues.append({**base, 'code': 'active_site_link_missing', 'severity': 'medium'})
            continue
        if not is_company_site_listing_url(direct_url):
            issues.append({**base, 'code': 'invalid_company_site_link', 'severity': 'high'})
            continue
        candidate = by_key.get(key or '')
        if candidate is None:
            issues.append({**base, 'code': 'active_site_link_not_in_catalog', 'severity': 'high'})
            continue
        matched_keys.add(candidate['site_key'])
        if candidate['publication_state'] == 'sold':
            issues.append(
                {**base, 'code': 'active_listing_marked_sold_on_site', 'severity': 'high', 'site': candidate}
            )
        elif candidate['publication_state'] == 'unknown':
            issues.append(
                {**base, 'code': 'site_publication_state_unknown', 'severity': 'medium', 'site': candidate}
            )
        if _same_price(record['price'], candidate['price']) is False:
            issues.append(
                {
                    **base,
                    'code': 'company_site_price_mismatch',
                    'severity': 'medium',
                    'marketing_price': record['price'],
                    'site_price': candidate['price'],
                    'site': candidate,
                }
            )

    for candidate in candidates:
        if candidate['publication_state'] == 'published' and candidate['site_key'] not in matched_keys:
            issues.append(
                {
                    'code': 'published_site_listing_unmapped',
                    'severity': 'medium',
                    'vehicle_key': candidate['site_key'],
                    'vin': None,
                    'site': candidate,
                }
            )

    severity = {level: sum(issue['severity'] == level for issue in issues) for level in ('high', 'medium', 'low')}
    return {
        'schema_version': 1,
        'generated_at': datetime.now(UTC).isoformat(),
        'source': {key: source[key] for key in ('requested_url', 'final_url', 'http_status')},
        'summary': {
            'active_marketing_records': len(active),
            'catalogue_candidates': len(candidates),
            'published_catalogue_candidates': sum(row['publication_state'] == 'published' for row in candidates),
            'issues_total': len(issues),
            'issues_by_severity': severity,
        },
        'candidates': candidates,
        'issues': issues,
        'limitations': [
            'Website catalogue membership proves publication on A1Auto.ru, not marketplace visibility.',
            'Active marketing rows without a direct A1Auto link are review items, not proof that a vehicle is absent from the site.',
            'No VIN is required when a stable A1Auto vehicle-card URL exists.',
        ],
    }
