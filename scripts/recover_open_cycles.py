#!/usr/bin/env python3
"""Recover interrupted local cycles through the MacBook host database DSN.

This is deliberately separate from ``app.cli recover-open-cycles``: the
container's internal DATABASE_DSN is not the host's loopback port.  It runs
only inside the verified interactive MacBook runner and never opens Chrome or
contacts a marketplace.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import quote_plus

try:  # Package import for tests and module execution from the project root.
    from scripts.local_scan import (
        HOST_RUNNER_CONTEXT_ENV,
        PROJECT_ROOT,
        _env_file_values,
        _require_verified_host_lock_context,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from local_scan import (  # type: ignore[no-redef]
        HOST_RUNNER_CONTEXT_ENV,
        PROJECT_ROOT,
        _env_file_values,
        _require_verified_host_lock_context,
    )


def configure_host_database(env_values: dict[str, str]) -> None:
    """Set the private host-to-Docker DSN used by every local Mac cycle."""
    password = env_values.get('DB_PASSWORD')
    if not password:
        raise RuntimeError('DB_PASSWORD is missing in .env')
    port = env_values.get('DB_BIND_PORT', '5433')
    os.environ['DATABASE_DSN'] = (
        'postgresql+psycopg2://monitor:'
        f'{quote_plus(password)}@127.0.0.1:{port}/a1_search_monitor'
    )


def main() -> int:
    if sys.platform == 'darwin':
        if os.environ.get(HOST_RUNNER_CONTEXT_ENV) != '1':
            raise RuntimeError('recovery requires run_monitoring_host_macos.sh')
        _require_verified_host_lock_context()

    configure_host_database(_env_file_values(PROJECT_ROOT / '.env'))
    from app.db import get_db_context
    from app.service.cycle import MonitoringCycleService

    with get_db_context() as db:
        result = MonitoringCycleService(db).recover_open_cycles(actor='macos_host_runner')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'OPEN_CYCLE_RECOVERY_FAILED {type(exc).__name__}', file=sys.stderr)
        raise SystemExit(1) from None
