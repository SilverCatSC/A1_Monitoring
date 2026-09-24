#!/usr/bin/env python3
"""Validate exported marketplace-feed identity columns without publishing."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.service.feed_identity_audit import audit_autoru_identity_rows, audit_avito_identity_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('auto_ru', 'avito'), required=True)
    parser.add_argument('--input', type=Path, required=True, help='UTF-8 CSV export of one feed tab')
    parser.add_argument(
        '--header-row', type=int, default=None,
        help='1-based header row in the Sheet export; defaults to 2 for Auto.ru, 1 for Avito',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    header_row = args.header_row or (2 if args.platform == 'auto_ru' else 1)
    if header_row < 1:
        raise SystemExit('--header-row must be at least 1')
    with args.input.open(encoding='utf-8-sig', newline='') as source:
        lines = list(csv.reader(source))
    if len(lines) < header_row:
        raise SystemExit(f'CSV has no header at row {header_row}')
    headers = lines[header_row - 1]
    rows = [dict(zip(headers, values, strict=False)) for values in lines[header_row:]]
    first_data_row = header_row + 1

    audit = (
        audit_autoru_identity_rows(rows, first_data_row=first_data_row)
        if args.platform == 'auto_ru'
        else audit_avito_identity_rows(rows, first_data_row=first_data_row)
    )
    print(
        json.dumps(
            {
                'platform': audit.platform,
                'checked_rows': audit.checked_rows,
                'valid_placement_ids': len(audit.placement_ids),
                'ready_to_publish': audit.is_ready,
                'findings': [finding.__dict__ for finding in audit.findings],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if audit.is_ready else 2


if __name__ == '__main__':
    raise SystemExit(main())
