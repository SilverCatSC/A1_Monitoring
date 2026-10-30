"""Read the three outbound placement feeds from the configured source workbook.

Only the existing Google Sheets CSV-export boundary is allowed. A cycle must
not treat a failed or partial feed fetch as an empty feed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse

import httpx

from app.service.source_recovery import source_with_recovery
from app.service.vehicle_content import vin_values

logger = logging.getLogger(__name__)


def _get_export(client, url):
    return source_with_recovery(lambda: _get_export_attempts(client, url), phase='workbook_export')


def _get_export_attempts(client, url):
    """Retry temporary download failures; never replace a failed sheet with old data."""
    for attempt in range(1, 4):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {
                429, 500, 502, 503, 504,
            }:
                raise
            if attempt == 3:
                raise
            logger.warning('Workbook export failed (%s); retry %d/3 in %ds',
                           type(exc).__name__, attempt + 1, attempt * 3)
            time.sleep(attempt * 3)

FEED_SPECS = {
    'autoru-feed-all': (779695926, 2, 3, {'unique_id', 'action', 'vin'}),
    'avito-feed-new': (2031892118, 1, 4, {'Id', 'AvitoId', 'VIN'}),
    'avito-feed-used': (727326859, 1, 4, {'Id', 'AvitoId', 'VIN'}),
}
MAX_FEED_BYTES = 8 * 1024 * 1024


class PlacementFeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlacementFeedSnapshot:
    rows_by_sheet: dict[str, list[dict[str, str]]]
    sha256_by_sheet: dict[str, str]
    first_data_rows: dict[str, int]
    marketing_rows: list[list[str]] = field(default_factory=list)
    marketing_sha256: str | None = None
    marketing_error: str | None = None
    workbook_url: str | None = None
    read_started_at_utc: str | None = None
    read_finished_at_utc: str | None = None
    feed_structure: dict = field(default_factory=dict)

    def cell_locations(self, sheet, row, key, default_column):
        return self.feed_structure.get(sheet, {}).get('cell_sources', {}).get(str(row), {}).get(
            key, [{'row': row, 'column': default_column}])

    def field_values(self, sheet, row, key, default):
        return self.feed_structure.get(sheet, {}).get('field_values', {}).get(str(row), {}).get(key, [default])


def parse_feed_csv(content: bytes, sheet: str):
    """Parse each header independently; preserve physical row offsets for callers.

    Compatible copies within one sheet can establish one logical feed record.
    No last/first-block preference: conflicting values are blanked and recorded.
    Contradictory identity claims remain separate and fail existing ID guards.
    """
    _, header_row, first_data_row, required = FEED_SPECS[sheet]
    rows = list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
    if len(rows) < max(header_row, first_data_row - 1):
        raise PlacementFeedError(f'{sheet}: missing header or first data row')
    id_key, vin_key = ('unique_id', 'vin') if sheet == 'autoru-feed-all' else ('Id', 'VIN')
    data = [{} for _ in rows[first_data_row - 1:]]
    structure = {'blocks': [], 'merged_duplicates': [], 'unresolved_duplicates': [], 'cell_sources': {}, 'field_values': {}}
    canonical_names = {key.casefold(): key for key in required}

    def set_header(values, number):
        header = [v.strip() for v in values]
        while header and not header[-1]:
            header.pop()
        folded = [key.casefold() for key in header]
        if len(folded) != len(set(folded)) or not {key.casefold() for key in required}.issubset(folded):
            raise PlacementFeedError(f'{sheet}: identity columns are missing or duplicated at row {number}')
        structure['blocks'].append({'header_row': number, 'columns': header})
        # Preserve the source header for diagnostics, but use one spelling for
        # each logical field across blocks so casing cannot hide a conflict.
        return [canonical_names.setdefault(key.casefold(), key) for key in header]

    header = set_header(rows[header_row - 1], header_row)
    explanations_remaining = 0
    for number, values in enumerate(rows[first_data_row - 1:], first_data_row):
        if not any(v.strip() for v in values):
            continue
        # The exact ID column label is not a vehicle ID. Reject incomplete new
        # schemas instead of reading their data with the preceding header.
        if (values and values[0].strip().casefold() == id_key.casefold()) or {
            key.casefold() for key in required
        }.issubset(v.strip().casefold() for v in values):
            header = set_header(values, number)
            explanations_remaining = 2 if sheet != 'autoru-feed-all' else 0
            continue
        if explanations_remaining:
            marker = values[header.index(id_key)].strip() if len(values) > header.index(id_key) else ''
            if marker in {'Обязательный', 'Необязательный'} or marker.startswith('Подробнее'):
                explanations_remaining -= 1
                continue
            explanations_remaining = 0
        if len(values) > len(header) and any(v.strip() for v in values[len(header):]):
            raise PlacementFeedError(f'{sheet}: CSV row wider than current header at row {number}')
        record = dict(zip(header, values[:len(header)], strict=False))
        data[number - first_data_row] = record
        structure['cell_sources'][str(number)] = {
            key: [{'row': number, 'column': col}] for col, key in enumerate(header, 1) if key in record}

    grouped = defaultdict(list)
    for index, row in enumerate(data):
        uid = row.get(id_key, '').strip()
        if uid:
            grouped[uid].append(index)
    for uid, indices in grouped.items():
        if len(indices) < 2:
            continue
        records = [data[i] for i in indices]
        vins = [vin_values(r.get(vin_key)) for r in records]
        nonempty_vins = [v for v in vins if v]
        platform_ids = {r.get('AvitoId', '').strip() for r in records} - {''}
        actions = {r.get('action', '').strip().casefold() for r in records} - {''}
        # A malformed nonblank VIN is not a missing value to fill silently.
        invalid_vin = any(r.get(vin_key, '').strip() and len(v) != 1 for r, v in zip(records, vins, strict=True))
        consistent_vins = not invalid_vin and (not nonempty_vins or len(set().union(*nonempty_vins)) == 1)
        shared_vin = all(vins) and consistent_vins
        shared_platform = len(platform_ids) == 1 and all(r.get('AvitoId', '').strip() for r in records)
        compatible = consistent_vins and len(platform_ids) <= 1 and len(actions) <= 1 and (shared_vin or shared_platform)
        row_numbers = [i + first_data_row for i in indices]
        if not compatible:
            structure['unresolved_duplicates'].append({'unique_id': uid, 'rows': row_numbers})
            continue
        primary = row_numbers[0]
        merged, locations, conflicts, variants = {}, {}, [], {}
        for key in dict.fromkeys(k for r in records for k in r):
            values = {r.get(key, '').strip() for r in records} - {''}
            merged[key] = next(iter(values)) if len(values) == 1 else ''
            if len(values) > 1:
                conflicts.append(key)
                variants[key] = sorted(values)
            locations[key] = [
                {**loc, **({'variant': variants[key].index(r[key].strip())} if key in variants else {})}
                for n, r in zip(row_numbers, records, strict=True) if r.get(key, '').strip()
                for loc in structure['cell_sources'][str(n)].get(key, [])]
        data[indices[0]] = merged
        for i in indices[1:]:
            data[i] = {}
        structure['cell_sources'][str(primary)] = locations
        structure['field_values'][str(primary)] = variants
        structure['merged_duplicates'].append({'unique_id': uid, 'rows': row_numbers, 'field_conflicts': conflicts})
    return data, structure


def workbook_id_from_source(source_url: str | None) -> str:
    """Do not infer a workbook from arbitrary URLs or local CSV paths."""
    parsed = urlparse(str(source_url or ''))
    parts = parsed.path.strip('/').split('/')
    if (
        parsed.scheme != 'https'
        or parsed.hostname != 'docs.google.com'
        or len(parts) < 3
        or parts[:2] != ['spreadsheets', 'd']
        or not re.fullmatch(r'[A-Za-z0-9_-]+', parts[2])
    ):
        raise PlacementFeedError('configured source is not a Google Sheets workbook URL')
    return parts[2]


def read_placement_feed_snapshot(
    source_url: str | None,
    *,
    client: httpx.Client | None = None,
) -> PlacementFeedSnapshot:
    started = datetime.now(UTC).isoformat()
    workbook_id = workbook_id_from_source(source_url)
    rows_by_sheet = {}
    sha256_by_sheet = {}
    first_data_rows = {}
    feed_structure = {}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30, follow_redirects=True)
    marketing_rows, marketing_sha256, marketing_error = [], None, None
    try:
        for sheet, (gid, _header_row, first_data_row, _required) in FEED_SPECS.items():
            url = f'https://docs.google.com/spreadsheets/d/{workbook_id}/export?' + urlencode(
                {'format': 'csv', 'gid': gid}
            )
            response = _get_export(client, url)
            content_type = response.headers.get('content-type', '').split(';')[0].lower()
            if content_type not in {'text/csv', 'application/octet-stream'}:
                raise PlacementFeedError(f'{sheet}: expected CSV export, got {content_type or "unknown"}')
            if len(response.content) > MAX_FEED_BYTES:
                raise PlacementFeedError(f'{sheet}: CSV export exceeds size limit')
            data, feed_structure[sheet] = parse_feed_csv(response.content, sheet)
            rows_by_sheet[sheet] = data
            sha256_by_sheet[sheet] = hashlib.sha256(response.content).hexdigest()
            first_data_rows[sheet] = first_data_row
        # Content comparison is independently unavailable when the main sheet fails.
        # Never discard successfully read identity feeds or treat failure as an empty table.
        try:
            response = _get_export(client,
                f'https://docs.google.com/spreadsheets/d/{workbook_id}/export?format=csv&gid=0'
            )
            response.raise_for_status()
            if (
                response.headers.get('content-type', '').split(';')[0].lower()
                not in {'text/csv', 'application/octet-stream'}
                or len(response.content) > MAX_FEED_BYTES
            ):
                raise PlacementFeedError('marketing sheet: invalid CSV export')
            marketing_rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
            expected = {
                5: 'Статус',
                9: 'Комплектация А1',
                10: 'VIN номер',
                11: 'Стоимость',
                12: 'Год выпуска',
                13: 'Пробег',
                15: 'НДС',
                22: 'AUTO-RU',
                23: 'AVITO-RU',
            }
            if not marketing_rows or any(
                len(marketing_rows[0]) <= col or marketing_rows[0][col].strip() != label
                for col, label in expected.items()
            ):
                raise PlacementFeedError('marketing sheet: expected columns changed')
            marketing_sha256 = hashlib.sha256(response.content).hexdigest()
        except (httpx.HTTPError, UnicodeError, csv.Error, PlacementFeedError) as exc:
            marketing_rows = []
            marketing_error = f'marketing snapshot unavailable: {type(exc).__name__}'
    except (httpx.HTTPError, UnicodeError, csv.Error) as exc:
        raise PlacementFeedError(f'outbound feed snapshot unavailable: {type(exc).__name__}') from exc
    finally:
        if owns_client:
            client.close()
    return PlacementFeedSnapshot(
        rows_by_sheet,
        sha256_by_sheet,
        first_data_rows,
        marketing_rows,
        marketing_sha256,
        marketing_error,
        f'https://docs.google.com/spreadsheets/d/{workbook_id}/edit',
        started,
        datetime.now(UTC).isoformat(),
        feed_structure,
    )
