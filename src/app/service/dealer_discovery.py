from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import DealerDiscoveryRun, DealerListingCandidate, EngineType
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.scraper.pacing import choose_pause
from app.scraper.seller import source_challenged
from app.service.locks import operation_lock


class DiscoveryAlreadyRunning(RuntimeError):
    pass


_local_discovery_lock = threading.Lock()
_POSTGRES_DISCOVERY_LOCK_KEY = 4_101_002


class DealerDiscoveryService:
    def __init__(self, db: Session, auto_adapter=None, avito_adapter=None, progress_callback=None):
        self.db = db
        self.progress = progress_callback or (lambda event: None)
        self.adapters = {
            EngineType.AUTO_RU: auto_adapter
            or AutoRuAdapter(request_timeout=settings.request_timeout_seconds, progress_callback=progress_callback),
            EngineType.AVITO: avito_adapter
            or AvitoAdapter(request_timeout=settings.request_timeout_seconds, progress_callback=progress_callback),
        }

    def run(self, *, sources=None, strict=False) -> dict:
        with operation_lock(self.db, _POSTGRES_DISCOVERY_LOCK_KEY, _local_discovery_lock,
                            DiscoveryAlreadyRunning, 'another dealer discovery is already running'):
            return self._run(sources=sources, strict=strict)

    def _run(self, *, sources=None, strict=False) -> dict:
        summary = {'sources': 0, 'complete': 0, 'failed': 0, 'candidates': 0}
        configured = settings.dealer_sources if sources is None else sources
        if strict:
            summary.update(run_ids=[], blocked_sources=[])
        for source in (EngineType.AUTO_RU, EngineType.AVITO):
            for dealer_url in configured.get(source.value, []):
                if strict and source.value in summary['blocked_sources']:
                    break
                summary['sources'] += 1
                started = datetime.now(UTC)
                record = DealerDiscoveryRun(
                    source=source,
                    dealer_url=dealer_url,
                    network_profile=settings.network_profile,
                    started_at=started,
                )
                self.db.add(record)
                self.db.flush()
                if strict:
                    summary['run_ids'].append(record.id)
                    pause = choose_pause(settings.scan_filter_pause_min_seconds, settings.scan_filter_pause_max_seconds)
                    self.progress({'event': 'dealer_catalogue_started', 'source': source.value, 'url': dealer_url, 'wait_seconds': round(pause, 1)})
                    time.sleep(pause)
                try:
                    result = asyncio.run(
                        self.adapters[source].scan_filter(
                            dealer_url, settings.seller_preflight_pages if strict else settings.dealer_pages_limit,
                            **({'seller_catalogue': True} if strict else {}),
                        )
                    )
                    # Reaching a page cap is adequate for top-N search, not for a whole seller inventory.
                    record.complete = result.complete and (result.exhausted or not strict)
                    record.pages_scanned = result.page_count
                    record.candidates_found = len(result.hits)
                    record.error = result.error or ('Catalogue end not proven within the page limit' if strict and not result.exhausted else None)
                    record.diagnostics = result.diagnostics
                    self._apply_observed_hits(
                        source,
                        dealer_url,
                        result.hits,
                        started,
                        deactivate_missing=record.complete,
                        run_id=record.id,
                    )
                    summary['candidates'] += len(result.hits)
                    if record.complete:
                        summary['complete'] += 1
                    else:
                        summary['failed'] += 1
                    if strict and source_challenged(result.error, result.diagnostics):
                        summary['blocked_sources'].append(source.value)
                except Exception as exc:
                    record.error = f'{type(exc).__name__}: {exc}'
                    summary['failed'] += 1
                    if strict and source_challenged(record.error, {}):
                        summary['blocked_sources'].append(source.value)
                record.finished_at = datetime.now(UTC)
                self.db.commit()
                if strict:
                    self.progress({'event': 'dealer_catalogue_finished', 'source': source.value, 'candidates': record.candidates_found,
                                   'complete': record.complete, 'error': record.error})
        return summary

    def _apply_observed_hits(
        self, source, dealer_url, hits, observed_at, *, deactivate_missing: bool, run_id=None
    ) -> None:
        if deactivate_missing:
            self.db.query(DealerListingCandidate).filter(
                DealerListingCandidate.source == source,
                DealerListingCandidate.dealer_url == dealer_url,
                DealerListingCandidate.active.is_(True),
            ).update({'active': False}, synchronize_session=False)
        seen = set()
        for hit in hits:
            key = canonical_listing_key(source, hit.url)
            if not is_marketplace_listing_url(source, hit.url) or key in seen:
                continue
            seen.add(key)
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
                    network_profile=settings.network_profile,
                    first_seen_at=observed_at,
                )
                self.db.add(candidate)
            candidate.dealer_url = dealer_url
            candidate.listing_url = hit.url
            candidate.network_profile = settings.network_profile
            candidate.title = hit.title
            candidate.price_hint = hit.price
            candidate.active = True
            candidate.last_seen_at = observed_at
            candidate.raw_payload = {**hit.raw, 'discovery_run_id': run_id}
