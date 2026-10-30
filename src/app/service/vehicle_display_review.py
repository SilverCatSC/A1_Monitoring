"""Read snapshot-bound owner confirmations for display, never advert identity."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from app.models import EngineType
from app.scraper.base import canonical_listing_key


def _read_review(evidence_dir, report):
    try:
        cycle = str(UUID(report['cycle_id']))
        directory = Path(evidence_dir) / 'cycles' / cycle
        path = directory / 'vehicle_display_review.json'
        original = directory / 'placement_reconciliation.json'
        if path.is_symlink() or original.is_symlink() or path.stat().st_size > 65536:
            return {}
        data = json.loads(path.read_text())
        if (data.get('schema_version') != 1 or data.get('cycle_id') != cycle
                or data.get('report_sha256') != hashlib.sha256(original.read_bytes()).hexdigest()
                or not isinstance(data.get('bindings'), list)):
            return {}
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def load_display_bindings(evidence_dir, report):
    data = _read_review(evidence_dir, report)
    return [b for b in data.get('bindings', []) if isinstance(b, dict)
                and all(isinstance(b.get(k), str) and b[k].strip()
                        for k in ('source', 'url', 'unique_id', 'basis', 'confirmed_by', 'recorded_on'))]


def load_display_photos(evidence_dir, report):
    """Only exact published UID + platform ID + an intact feed gallery row."""
    data = _read_review(evidence_dir, report)
    result = []
    entries = data.get('photos', [])
    if not isinstance(entries, list) or len(entries) > 100:
        return []
    for entry in entries:
        try:
            if entry['source'] != 'avito' or entry['feed_sheet'] not in {'avito-feed-new', 'avito-feed-used'}:
                continue
            name = entry['snapshot_file']
            if not isinstance(name, str) or Path(name).name != name:
                continue
            path = Path(evidence_dir) / 'cycles' / str(UUID(report['cycle_id'])) / name
            if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['snapshot_sha256']:
                continue
            snapshot = json.loads(raw)
            rows = [row for sheet, values in snapshot['rows_by_sheet'].items()
                    if sheet.startswith('avito-feed-') for row in values
                    if row.get('Id') == entry['unique_id']]
            if len(rows) != 1 or entry['url'] not in [c['url'] for c in report['observed_cards']
                    if c.get('state') == 'active' and c.get('source') == 'avito'
                    and c.get('unique_id') == entry['unique_id']]:
                continue
            row = rows[0]
            if canonical_listing_key(EngineType.AVITO, entry['url']) != 'avito:' + row.get('AvitoId', ''):
                continue
            images = re.findall(r'https://[^\s<>"|]+', row.get('ImageUrls') or '')
            image = urlsplit(entry['image_url'])
            if (entry['image_url'] not in images or image.hostname != 'storage.yandexcloud.net'
                    or not image.path.startswith('/a1autofeed/') or image.username or image.password
                    or image.port not in {None, 443}):
                continue
            result.append(entry)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return result
