#!/usr/bin/env python3
"""Write a read-only head-office-table reconciliation artifact."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus


def _local_database_environment(root: Path) -> None:
    """Match the host-side connection used by the visible Chrome worker."""
    values: dict[str, str] = {}
    env_file = root / '.env'
    if env_file.exists():
        for raw in env_file.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    password = values.get('DB_PASSWORD')
    if password and not os.environ.get('DATABASE_DSN'):
        port = values.get('DB_BIND_PORT', '5433')
        os.environ['DATABASE_DSN'] = (
            'postgresql+psycopg2://monitor:'
            f'{quote_plus(password)}@127.0.0.1:{port}/a1_search_monitor'
        )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    _local_database_environment(root)
    from app.db import get_db_context
    from app.service.head_table_audit import audit_head_table

    target = root / 'artifacts' / 'head_table_audits'
    target.mkdir(parents=True, exist_ok=True)
    with get_db_context() as db:
        report = audit_head_table(db)
    stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
    path = target / f'audit_{stamp}.json'
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    path.write_text(payload, encoding='utf-8')
    (target / 'latest.json').write_text(payload, encoding='utf-8')
    print(f'HEAD_TABLE_AUDIT_READY {path}')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
