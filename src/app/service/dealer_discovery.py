from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DealerDiscoveryRun, DealerListingCandidate, EngineType
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter
from app.scraper.base import canonical_listing_key


class DiscoveryAlreadyRunning(RuntimeError):
    pass


_local_discovery_lock = threading.Lock()
_POSTGRES_DISCOVERY_LOCK_KEY = 4_101_002


class DealerDiscoveryService:
    def __init__(self, db: Session, auto_adapter=None, avito_adapter=None):
        self.db = db
        self.adapters = {
            EngineType.AUTO_RU: auto_adapter
            or AutoRuAdapter(request_timeout=settings.request_timeout_seconds),
            EngineType.AVITO: avito_adapter
            or AvitoAdapter(request_timeout=settings.request_timeout_seconds),
        }

    def run(self) -> dict:
        postgres = self.db.bind is not None and self.db.bind.dialect.name == 'postgresql'
        if postgres:
            acquired = bool(
                self.db.execute(
                    text('SELECT pg_try_advisory_lock(:key)'),
                    {'key': _POSTGRES_DISCOVERY_LOCK_KEY},
                ).scalar()
            )
        else:
            acquired = _local_discovery_lock.acquire(blocking=False)
        if not acquired:
            raise DiscoveryAlreadyRunning('another dealer discovery is already running')
        try:
            return self._run()
        finally:
            if postgres:
                self.db.execute(
                    text('SELECT pg_advisory_unlock(:key)'),
                    {'key': _POSTGRES_DISCOVERY_LOCK_KEY},
                )
            else:
                _local_discovery_lock.release()

    def _run(self) -> dict:
        summary = {'sources': 0, 'complete': 0, 'failed': 0, 'candidates': 0}
        configured = settings.dealer_sources
        for source in (EngineType.AUTO_RU, EngineType.AVITO):
            for dealer_url in configured[source.value]:
                summary['sources'] += 1
                started = datetime.now(UTC)
                record = DealerDiscoveryRun(
                    source=source,
                    dealer_url=dealer_url,
                    started_at=started,
                )
                self.db.add(record)
                self.db.flush()
                try:
                    result = asyncio.run(
                        self.adapters[source].scan_filter(
                            dealer_url, settings.dealer_pages_limit
                        )
                    )
                    record.complete = result.complete
                    record.pages_scanned = result.page_count
                    record.candidates_found = len(result.hits)
                    record.error = result.error
                    record.diagnostics = result.diagnostics
                    if result.complete:
                        self._apply_complete_snapshot(source, dealer_url, result.hits, started)
                        summary['complete'] += 1
                        summary['candidates'] += len(result.hits)
                    else:
                        summary['failed'] += 1
                except Exception as exc:
                    record.error = f'{type(exc).__name__}: {exc}'
                    summary['failed'] += 1
                record.finished_at = datetime.now(UTC)
                self.db.commit()
        return summary

    def _apply_complete_snapshot(self, source, dealer_url, hits, observed_at) -> None:
        self.db.query(DealerListingCandidate).filter(
            DealerListingCandidate.source == source,
            DealerListingCandidate.dealer_url == dealer_url,
            DealerListingCandidate.active.is_(True),
        ).update({'active': False}, synchronize_session=False)
        for hit in hits:
            key = canonical_listing_key(source, hit.url)
            if not key:
                continue
            candidate = (
                self.db.query(DealerListingCandidate)
                .filter(
                    DealerListingCandidate.source == source,
                    DealerListingCandidate.external_key == key,
                )
                .one_or_none()
            )
            if candidate is None:
                candidate = DealerListingCandidate(
                    source=source,
                    external_key=key,
                    dealer_url=dealer_url,
                    listing_url=hit.url,
                    first_seen_at=observed_at,
                )
                self.db.add(candidate)
            candidate.dealer_url = dealer_url
            candidate.listing_url = hit.url
            candidate.title = hit.title
            candidate.price_hint = hit.price
            candidate.active = True
            candidate.last_seen_at = observed_at
            candidate.raw_payload = hit.raw
