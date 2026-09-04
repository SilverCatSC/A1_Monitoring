from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ScanDiagnostics:
    run_id: str
    source: str
    filter_name: str
    started_at: datetime
    ended_at: datetime | None = None
    pages: int = 0
    hits: int = 0
    errors: list[str] | None = None
    page_metrics: dict[str, int] | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.ended_at is None:
            return 0
        return (self.ended_at - self.started_at).total_seconds()

    @property
    def fingerprint(self) -> str:
        h = hashlib.sha1(f'{self.run_id}{self.source}{self.filter_name}'.encode('utf-8')).hexdigest()
        return h
