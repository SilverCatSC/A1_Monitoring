from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path


def cleanup_evidence(evidence_dir: str, retention_days: int) -> int:
    root = Path(evidence_dir).resolve()
    if not root.exists() or not root.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    removed = 0
    for path in root.glob('*.png'):
        if not path.is_file() or path.is_symlink():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed
