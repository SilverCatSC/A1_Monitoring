from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AbsenceEpisode,
    EngineType,
    ListingObservation,
    ObservationState,
    ScanRun,
    ScanRunStatus,
    SearchFilter,
    VehicleFilterExpectation,
)
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter
from app.scraper.base import ListingHit, ScanResult, canonical_listing_key


class ScanAlreadyRunning(RuntimeError):
    pass


_local_scan_lock = threading.Lock()
_POSTGRES_SCAN_LOCK_KEY = 4_101_001


class MonitorService:
    def __init__(self, db: Session, auto_adapter=None, avito_adapter=None):
        self.db = db
        self.auto_adapter = auto_adapter or AutoRuAdapter(
            request_timeout=settings.request_timeout_seconds
        )
        self.avito_adapter = avito_adapter or AvitoAdapter(
            request_timeout=settings.request_timeout_seconds
        )

    def _active_filters(self, source: EngineType) -> list[SearchFilter]:
        return (
            self.db.query(SearchFilter)
            .filter(SearchFilter.source == source, SearchFilter.active.is_(True))
            .all()
        )

    def _all_expectations(self, filter_id: str) -> list[VehicleFilterExpectation]:
        return (
            self.db.query(VehicleFilterExpectation)
            .filter(VehicleFilterExpectation.filter_id == filter_id)
            .all()
        )

    def _record_observation(
        self,
        run: ScanRun,
        listing_id: str,
        filter_id: str,
        source: EngineType,
        state: ObservationState,
        hit: ListingHit | None = None,
        diagnostics: dict | None = None,
    ) -> None:
        found = state == ObservationState.FOUND
        raw_payload = dict(hit.raw) if hit else {}
        if diagnostics:
            raw_payload['scan_diagnostics'] = diagnostics
        if hit:
            raw_payload['canonical_listing_key'] = canonical_listing_key(source, hit.url)

        self.db.add(
            ListingObservation(
                run_id=run.id,
                listing_id=listing_id,
                filter_id=filter_id,
                source=source,
                page_number=hit.page_number if hit else 0,
                position_in_page=hit.position if hit else 0,
                found=found,
                state=state,
                listing_url=hit.url if hit else None,
                title=hit.title if hit else None,
                price_hint=hit.price if hit else None,
                matched_by='marketplace_listing_id' if hit else None,
                raw_payload=raw_payload,
                observed_at=datetime.now(UTC),
            )
        )

    def _previous_miss_streak(
        self, listing_id: str, filter_id: str, source: EngineType
    ) -> tuple[int, datetime | None]:
        rows = (
            self.db.query(ListingObservation)
            .filter(
                ListingObservation.listing_id == listing_id,
                ListingObservation.filter_id == filter_id,
                ListingObservation.source == source,
            )
            .order_by(ListingObservation.observed_at.desc(), ListingObservation.id.desc())
            .limit(50)
            .all()
        )
        count = 0
        earliest = None
        for row in rows:
            if row.state == ObservationState.FOUND:
                break
            if row.state in {
                ObservationState.ABSENT_UNCERTAIN,
                ObservationState.ABSENT_CONFIRMED,
            }:
                count += 1
                earliest = row.observed_at
            elif row.state == ObservationState.TECHNICAL_ERROR:
                # A technical failure neither proves absence nor resets a validated miss streak.
                continue
            else:
                break
        return count, earliest

    def _record_technical_filter_failure(
        self,
        run: ScanRun,
        filter_entity: SearchFilter,
        source: EngineType,
        expectations: list[VehicleFilterExpectation],
        error: str,
        diagnostics: dict | None = None,
    ) -> None:
        run.technical_errors += 1
        run.notes = (run.notes or '') + f'[{filter_entity.id}] {error}\n'
        for expectation in expectations:
            self._record_observation(
                run,
                expectation.listing_id,
                filter_entity.id,
                source,
                ObservationState.TECHNICAL_ERROR,
                diagnostics={'error': error, **(diagnostics or {})},
            )

    def run_full_cycle(self) -> dict[str, int]:
        postgres = self.db.bind is not None and self.db.bind.dialect.name == 'postgresql'
        acquired = False
        if postgres:
            acquired = bool(
                self.db.execute(
                    text('SELECT pg_try_advisory_lock(:key)'),
                    {'key': _POSTGRES_SCAN_LOCK_KEY},
                ).scalar()
            )
        else:
            acquired = _local_scan_lock.acquire(blocking=False)
        if not acquired:
            raise ScanAlreadyRunning('another scan cycle is already running')
        try:
            return self._run_full_cycle()
        finally:
            if postgres:
                self.db.execute(
                    text('SELECT pg_advisory_unlock(:key)'),
                    {'key': _POSTGRES_SCAN_LOCK_KEY},
                )
            else:
                _local_scan_lock.release()

    def _run_full_cycle(self) -> dict[str, int]:
        started = datetime.now(UTC)
        summary = {
            'filters_scanned': 0,
            'found': 0,
            'missed_confirmed': 0,
            'missed_uncertain': 0,
            'technical_errors': 0,
            'runs': 0,
        }
        enabled = set(settings.scan_engines)

        for source in [EngineType.AUTO_RU, EngineType.AVITO]:
            if source.value not in enabled:
                continue
            filters = self._active_filters(source)
            scan_run = ScanRun(
                started_at=started,
                source=source,
                status=ScanRunStatus.IN_PROGRESS,
                filters_total=len(filters),
                notes=None,
            )
            self.db.add(scan_run)
            self.db.flush()
            summary['runs'] += 1

            if not filters:
                scan_run.technical_errors = 1
                scan_run.notes = 'No active filters configured; no visibility conclusion was made.\n'

            adapter = self.auto_adapter if source == EngineType.AUTO_RU else self.avito_adapter
            for filter_entity in filters:
                summary['filters_scanned'] += 1
                expectations = self._all_expectations(filter_entity.id)
                if not filter_entity.raw_url:
                    self._record_technical_filter_failure(
                        scan_run,
                        filter_entity,
                        source,
                        expectations,
                        'filter has no URL',
                    )
                    continue
                try:
                    scan_result: ScanResult = asyncio.run(
                        adapter.scan_filter(filter_entity.raw_url, settings.scan_pages_limit)
                    )
                except Exception as exc:  # adapter boundary: preserve an auditable technical result
                    self._record_technical_filter_failure(
                        scan_run,
                        filter_entity,
                        source,
                        expectations,
                        f'{type(exc).__name__}: {exc}',
                    )
                    continue

                scan_run.pages_scanned = max(scan_run.pages_scanned, scan_result.page_count)
                if not scan_result.complete:
                    self._record_technical_filter_failure(
                        scan_run,
                        filter_entity,
                        source,
                        expectations,
                        scan_result.error or 'incomplete page traversal',
                        scan_result.diagnostics,
                    )
                    continue

                scan_run.filters_ok += 1
                expected_by_key: dict[str, VehicleFilterExpectation] = {}
                for expectation in expectations:
                    direct_url = (
                        expectation.listing.source_auto_ru
                        if source == EngineType.AUTO_RU
                        else expectation.listing.source_avito
                    )
                    key = canonical_listing_key(source, direct_url)
                    if key:
                        expected_by_key[key] = expectation

                found_ids: set[str] = set()
                for hit in scan_result.hits:
                    key = canonical_listing_key(source, hit.url)
                    expectation = expected_by_key.get(key or '')
                    if expectation is None or expectation.listing_id in found_ids:
                        continue
                    found_ids.add(expectation.listing_id)
                    self._record_observation(
                        scan_run,
                        expectation.listing_id,
                        filter_entity.id,
                        source,
                        ObservationState.FOUND,
                        hit=hit,
                    )
                    expectation.listing.last_seen_at = datetime.now(UTC)
                    self._close_absence(
                        scan_run, expectation.listing_id, filter_entity.id, source
                    )
                    summary['found'] += 1

                for expectation in expectations:
                    if expectation.listing_id in found_ids:
                        continue
                    previous_misses, first_missing_at = self._previous_miss_streak(
                        expectation.listing_id, filter_entity.id, source
                    )
                    miss_count = previous_misses + 1
                    confirmed = miss_count >= settings.min_confirmed_absence_runs
                    state = (
                        ObservationState.ABSENT_CONFIRMED
                        if confirmed
                        else ObservationState.ABSENT_UNCERTAIN
                    )
                    self._record_observation(
                        scan_run,
                        expectation.listing_id,
                        filter_entity.id,
                        source,
                        state,
                        diagnostics=scan_result.diagnostics,
                    )
                    if confirmed:
                        self._upsert_absence(
                            scan_run,
                            expectation.listing_id,
                            filter_entity.id,
                            source,
                            miss_count,
                            first_missing_at or scan_run.started_at,
                        )
                        summary['missed_confirmed'] += 1
                    else:
                        summary['missed_uncertain'] += 1

            scan_run.completed_at = datetime.now(UTC)
            scan_run.finished_at = datetime.now(UTC)
            if scan_run.filters_total == 0:
                scan_run.status = ScanRunStatus.FAILED
            elif scan_run.filters_ok == scan_run.filters_total:
                scan_run.status = ScanRunStatus.SUCCESS
            elif scan_run.filters_ok == 0:
                scan_run.status = ScanRunStatus.FAILED
            else:
                scan_run.status = ScanRunStatus.PARTIAL
            summary['technical_errors'] += scan_run.technical_errors
            self.db.flush()

        self.db.commit()
        return summary

    def _upsert_absence(
        self,
        run: ScanRun,
        listing_id: str,
        filter_id: str,
        source: EngineType,
        miss_count: int,
        started_at: datetime,
    ) -> None:
        open_episode = (
            self.db.query(AbsenceEpisode)
            .filter(
                and_(
                    AbsenceEpisode.listing_id == listing_id,
                    AbsenceEpisode.filter_id == filter_id,
                    AbsenceEpisode.source == source,
                    AbsenceEpisode.open.is_(True),
                )
            )
            .one_or_none()
        )
        if open_episode is None:
            self.db.add(
                AbsenceEpisode(
                    listing_id=listing_id,
                    filter_id=filter_id,
                    source=source,
                    consecutive_misses=miss_count,
                    observed_count=miss_count,
                    last_missing_run_id=run.id,
                    started_at=started_at,
                    notes='confirmed_absence',
                )
            )
            return

        open_episode.consecutive_misses = miss_count
        open_episode.observed_count += 1
        open_episode.last_missing_run_id = run.id
        open_episode.notes = 'confirmed_absence'

    def _close_absence(
        self, run: ScanRun, listing_id: str, filter_id: str, source: EngineType
    ) -> None:
        open_episode = (
            self.db.query(AbsenceEpisode)
            .filter(
                and_(
                    AbsenceEpisode.listing_id == listing_id,
                    AbsenceEpisode.filter_id == filter_id,
                    AbsenceEpisode.source == source,
                    AbsenceEpisode.open.is_(True),
                )
            )
            .one_or_none()
        )
        if open_episode is None:
            return
        open_episode.open = False
        open_episode.ended_at = run.started_at
        open_episode.last_missing_run_id = run.id
