"""Read one cycle-owned placement report without trusting a stored path."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from app.models import MonitoringCycle

MAX_REPORT_BYTES = 4 * 1024 * 1024


class PlacementReportError(ValueError):
    pass


def read_cycle_placement_report(cycle: MonitoringCycle, evidence_dir: str) -> dict:
    """Return only a report created for this exact cycle, or fail closed."""
    cycle_id = str(cycle.id)
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', cycle_id):
        raise PlacementReportError('invalid_cycle_id')
    expected = Path('cycles') / cycle_id / 'placement_reconciliation.json'
    summary = cycle.summary or {}
    placement = summary.get('placement_reconciliation') if isinstance(summary, dict) else None
    if not isinstance(placement, dict) or placement.get('report_path') != expected.as_posix():
        raise PlacementReportError('report_not_available')
    root = Path(evidence_dir).resolve()
    target = root / expected
    for path in (root / 'cycles', root / 'cycles' / cycle_id, target):
        if path.is_symlink():
            raise PlacementReportError('invalid_report_path')
    try:
        target.resolve(strict=True).relative_to(root)
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES:
            raise PlacementReportError('invalid_report_file')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, 'rb') as source:
            raw = source.read(MAX_REPORT_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise PlacementReportError('report_unreadable') from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise PlacementReportError('invalid_report_file')
    try:
        report = json.loads(raw.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PlacementReportError('invalid_report_json') from exc
    if (not isinstance(report, dict) or report.get('schema_version') != 1
            or report.get('cycle_id') != cycle_id
            or not isinstance(report.get('findings'), list)
            or not isinstance(report.get('observed_cards'), list)):
        raise PlacementReportError('invalid_report_schema')
    findings = report['findings']
    cards = report['observed_cards']
    if (len(findings) > 5000 or len(cards) > 1000
            or any(not isinstance(item, dict) or not isinstance(item.get('code'), str)
                   or not isinstance(item.get('observed_urls'), list)
                   or any(not isinstance(url, str) for url in item['observed_urls'])
                   for item in findings)
            or any(not isinstance(item, dict) or not isinstance(item.get('url'), str)
                   or not isinstance(item.get('source'), str)
                   for item in cards)):
        raise PlacementReportError('invalid_report_schema')
    return report
