"""Read the three outbound placement feeds from the configured source workbook.

Only the existing Google Sheets CSV-export boundary is allowed. A cycle must
not treat a failed or partial feed fetch as an empty feed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse

import httpx

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
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30, follow_redirects=True)
    marketing_rows, marketing_sha256, marketing_error = [], None, None
    try:
        for sheet, (gid, header_row, first_data_row, required) in FEED_SPECS.items():
            url = f'https://docs.google.com/spreadsheets/d/{workbook_id}/export?' + urlencode(
                {'format': 'csv', 'gid': gid}
            )
            response = client.get(url)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '').split(';')[0].lower()
            if content_type not in {'text/csv', 'application/octet-stream'}:
                raise PlacementFeedError(f'{sheet}: expected CSV export, got {content_type or "unknown"}')
            if len(response.content) > MAX_FEED_BYTES:
                raise PlacementFeedError(f'{sheet}: CSV export exceeds size limit')
            rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
            if len(rows) < header_row:
                raise PlacementFeedError(f'{sheet}: missing header row')
            header = [value.strip() for value in rows[header_row - 1]]
            if len(header) != len(set(header)) or not required.issubset(header):
                raise PlacementFeedError(f'{sheet}: identity columns are missing or duplicated')
            if len(rows) < first_data_row - 1:
                raise PlacementFeedError(f'{sheet}: missing first data row')
            data = []
            for values in rows[first_data_row - 1 :]:
                if len(values) > len(header):
                    raise PlacementFeedError(f'{sheet}: CSV row wider than header')
                data.append(dict(zip(header, values, strict=False)))
            rows_by_sheet[sheet] = data
            sha256_by_sheet[sheet] = hashlib.sha256(response.content).hexdigest()
            first_data_rows[sheet] = first_data_row
        # Content comparison is independently unavailable when the main sheet fails.
        # Never discard successfully read identity feeds or treat failure as an empty table.
        try:
            response = client.get(
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
    )
