from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pandas as pd

from app.contracts import SourceRecord, canonicalize_headers, map_row

from .base import SourceReader

logger = logging.getLogger(__name__)


def _download_sheet(source: str) -> httpx.Response:
    """Retry transient export failures without importing a stale registry."""
    for attempt in range(1, 4):
        try:
            response = httpx.get(source, timeout=30, follow_redirects=True)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {
                429, 500, 502, 503, 504,
            }:
                raise
            if attempt == 3:
                raise RuntimeError(
                    'Google Sheets registry unavailable after 3 attempts '
                    f'({type(exc).__name__}). Check Google access and VPN routing; '
                    'marketplace checks have not started. No stale registry was used.'
                ) from exc
            delay = attempt * 3
            logger.warning(
                'Google Sheets registry download failed (%s); retry %d/3 in %ds',
                type(exc).__name__, attempt + 1, delay,
            )
            time.sleep(delay)


class CsvOrXlsxReader(SourceReader):
    def __init__(self, source: str):
        self.source = str(source)

    @classmethod
    def supports(cls, source: str) -> bool:
        lower = source.lower()
        return (
            lower.startswith('https://docs.google.com/spreadsheets/')
            or lower.endswith('.csv')
            or lower.endswith('.xlsx')
            or lower.endswith('.xls')
        )

    def read(self) -> list[SourceRecord]:
        if self.source.startswith('https://'):
            parsed = urlparse(self.source)
            if parsed.hostname != 'docs.google.com' or '/spreadsheets/' not in parsed.path:
                raise ValueError('Remote import is allowed only from docs.google.com/spreadsheets')
            response = _download_sheet(self.source)
            content_type = response.headers.get('content-type', '')
            if 'text/csv' not in content_type and 'application/octet-stream' not in content_type:
                raise ValueError(f'Expected CSV export, received {content_type or "unknown content type"}')
            df = pd.read_csv(io.BytesIO(response.content))
        else:
            path = Path(self.source)
            if not path.exists():
                raise FileNotFoundError(f'Source file not found: {self.source}')
            if self.source.lower().endswith('.csv'):
                df = pd.read_csv(self.source)
            else:
                df = pd.read_excel(self.source)
        headers_map = canonicalize_headers(df.columns.tolist())
        rows: list[SourceRecord] = []
        for i, row in df.iterrows():
            row_data = map_row(headers_map, row.to_dict())
            rows.append(SourceRecord(row_number=int(i + 2), source=row_data, raw=row.to_dict()))
        return rows
