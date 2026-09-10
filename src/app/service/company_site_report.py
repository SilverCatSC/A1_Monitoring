"""Safe dashboard projection of the latest read-only A1Auto catalogue audit.

The audit itself is created on the local host before/after a monitoring cycle.
This reader treats its JSON file as untrusted input: a missing or malformed file
is an operational state, never a favourable monitoring result.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.config import settings
from app.scraper.base import canonical_company_site_key


def _number(value: Any) -> int:
    """Return non-negative integer counters only."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(ZoneInfo('Europe/Moscow'))


def _safe_catalog_url(value: Any) -> str:
    """Keep an artifact from turning a dashboard link into an arbitrary URL."""
    raw = str(value or '').strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower().removeprefix('www.')
    path = parsed.path.rstrip('/')
    if parsed.scheme in {'http', 'https'} and host == 'a1auto.ru' and path == '/cars-for-sale':
        return raw
    return settings.company_site_catalog_url


def _safe_listing_url(value: Any) -> str | None:
    raw = str(value or '').strip()
    return raw if canonical_company_site_key(raw) else None


def company_site_audit_context(audit_dir: str | Path | None = None) -> dict[str, Any]:
    """Read ``latest.json`` into a bounded UI-safe projection."""
    directory = Path(audit_dir or settings.company_site_audit_dir)
    report_path = directory / 'latest.json'
    unavailable = {
        'state': 'not_run',
        'reason': 'Сверка каталога A1Auto ещё не запускалась.',
        'generated_at': None,
        'source_url': settings.company_site_catalog_url,
        'summary': None,
        'issues': [],
        'issues_total': 0,
        'limitations': [],
    }
    if not report_path.is_file():
        return unavailable
    try:
        raw = json.loads(report_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {**unavailable, 'state': 'invalid', 'reason': 'Файл сверки A1Auto повреждён или недоступен.'}
    if not isinstance(raw, dict) or raw.get('schema_version') != 1:
        return {**unavailable, 'state': 'invalid', 'reason': 'Формат отчёта A1Auto не распознан.'}

    summary = raw.get('summary')
    source = raw.get('source')
    raw_issues = raw.get('issues')
    if not isinstance(summary, dict) or not isinstance(source, dict) or not isinstance(raw_issues, list):
        return {**unavailable, 'state': 'invalid', 'reason': 'В отчёте A1Auto нет обязательных разделов.'}

    issues: list[dict[str, Any]] = []
    for issue in raw_issues[:100]:
        if not isinstance(issue, dict):
            continue
        site = issue.get('site') if isinstance(issue.get('site'), dict) else {}
        issues.append({
            'code': str(issue.get('code') or 'unknown'),
            'severity': str(issue.get('severity') or 'medium'),
            'vin': issue.get('vin') or None,
            'vehicle_key': str(issue.get('vehicle_key') or ''),
            'head_row': issue.get('head_row'),
            'direct_url': _safe_listing_url(issue.get('direct_url') or site.get('url')),
            'marketing_price': issue.get('marketing_price'),
            'site_price': issue.get('site_price') or site.get('price'),
            'site_state': site.get('publication_state') or None,
        })
    severity = summary.get('issues_by_severity') if isinstance(summary.get('issues_by_severity'), dict) else {}
    normalized_summary = {
        'active_marketing_records': _number(summary.get('active_marketing_records')),
        'catalogue_candidates': _number(summary.get('catalogue_candidates')),
        'published_catalogue_candidates': _number(summary.get('published_catalogue_candidates')),
        'issues_total': _number(summary.get('issues_total')),
        'issues_by_severity': {
            'high': _number(severity.get('high')),
            'medium': _number(severity.get('medium')),
            'low': _number(severity.get('low')),
        },
    }
    limitations = [str(item) for item in raw.get('limitations', []) if isinstance(item, str)][:5]
    return {
        'state': 'ready',
        'reason': None,
        'generated_at': _timestamp(raw.get('generated_at')),
        'source_url': _safe_catalog_url(source.get('final_url') or source.get('requested_url')),
        'summary': normalized_summary,
        'issues': issues,
        'issues_total': normalized_summary['issues_total'],
        'limitations': limitations,
    }
