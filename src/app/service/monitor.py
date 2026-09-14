from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import PRODUCTION_NETWORK_PROFILES, SCAN_ALLOWED_NETWORK_PROFILES, settings
from app.models import (
    AbsenceEpisode,
    EngineType,
    Listing,
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
from app.scraper.pacing import choose_pause
from app.service.locks import operation_lock


class ScanAlreadyRunning(RuntimeError):
    pass


class ScanConfigurationError(RuntimeError):
    pass


_local_scan_lock = threading.Lock()
_POSTGRES_SCAN_LOCK_KEY = 4_101_001


class MonitorService:
    def __init__(
        self,
        db: Session,
        auto_adapter=None,
        avito_adapter=None,
        progress_callback: Callable[[dict], None] | None = None,
        preflight: dict | None = None,
        cycle_id: str | None = None,
    ):
        self.db = db
        self.cycle_id = cycle_id
        self.progress_callback = progress_callback
        self.preflight = preflight
        self.auto_adapter = auto_adapter or AutoRuAdapter(
            request_timeout=settings.request_timeout_seconds,
            progress_callback=progress_callback,
        )
        self.avito_adapter = avito_adapter or AvitoAdapter(
            request_timeout=settings.request_timeout_seconds,
            progress_callback=progress_callback,
        )

    def _progress(self, event: str, **payload) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback({'event': event, **payload})
        except Exception:
            # Reporting must never change the monitoring fact.
            return

    def _active_filters(self, source: EngineType) -> list[SearchFilter]:
        return (
            self.db.query(SearchFilter)
            .filter(SearchFilter.source == source, SearchFilter.active.is_(True))
            .all()
        )

    def _all_expectations(self, filter_id: str) -> list[VehicleFilterExpectation]:
        return (
            self.db.query(VehicleFilterExpectation)
            .join(VehicleFilterExpectation.listing)
            .filter(VehicleFilterExpectation.filter_id == filter_id)
            .filter(VehicleFilterExpectation.listing.has(is_active=True))
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
        absolute_position: int | None = None,
    ) -> None:
        found = state == ObservationState.FOUND
        raw_payload = dict(hit.raw) if hit else {}
        listing = self.db.get(Listing, listing_id)
        search_filter = self.db.get(SearchFilter, filter_id)
        expected_url = listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
        raw_payload.update(
            expected_listing_key=canonical_listing_key(source, expected_url),
            expected_listing_url=expected_url,
            filter_version=search_filter.version,
            filter_url=search_filter.raw_url,
            app_version=settings.app_version,
        )
        if self.cycle_id is not None:
            raw_payload['cycle_id'] = self.cycle_id
        if self.preflight is not None:
            raw_payload['seller_preflight'] = self.preflight['checks'].get(f'{listing_id}:{source.value}', {})
            raw_payload['seller_preflight_batch'] = self.preflight['batch_id']
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
                absolute_position=absolute_position,
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
        self,
        listing_id: str,
        filter_id: str,
        source: EngineType,
        network_profile: str,
    ) -> tuple[int, datetime | None]:
        rows = (
            self.db.query(ListingObservation)
            .filter(
                ListingObservation.listing_id == listing_id,
                ListingObservation.filter_id == filter_id,
                ListingObservation.source == source,
                ListingObservation.scan_run.has(network_profile=network_profile),
            )
            .order_by(ListingObservation.observed_at.desc(), ListingObservation.id.desc())
            .limit(50)
            .all()
        )
        count = 0
        earliest = None
        listing = self.db.get(Listing, listing_id)
        expected_key = canonical_listing_key(
            source, listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
        )
        search_filter = self.db.get(SearchFilter, filter_id)
        changed_at = max((
            event.created_at for event in listing.link_events
            if event.source == source
            and canonical_listing_key(source, event.old_url) != canonical_listing_key(source, event.new_url)
        ), default=None)
        for row in rows:
            payload = row.raw_payload or {}
            if changed_at and row.observed_at.replace(tzinfo=UTC) < changed_at.replace(tzinfo=UTC):
                break
            if 'expected_listing_key' in payload and payload['expected_listing_key'] != expected_key:
                break
            if 'filter_version' in payload and payload['filter_version'] != search_filter.version:
                break
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

    def run_full_cycle(self) -> dict:
        if settings.network_profile not in SCAN_ALLOWED_NETWORK_PROFILES:
            allowed = ', '.join(sorted(SCAN_ALLOWED_NETWORK_PROFILES))
            raise ScanConfigurationError(
                f'scan requires one of [{allowed}]; current={settings.network_profile}'
            )
        with operation_lock(self.db, _POSTGRES_SCAN_LOCK_KEY, _local_scan_lock,
                            ScanAlreadyRunning, 'another scan cycle is already running'):
            try:
                return self._run_full_cycle()
            except Exception as exc:
                self._progress('cycle_failed', error=f'{type(exc).__name__}: {exc}')
                raise

    def _run_full_cycle(self) -> dict:
        started = datetime.now(UTC)
        summary = {
            'cycle_id': self.cycle_id,
            'filters_scanned': 0,
            'found': 0,
            'missed_confirmed': 0,
            'missed_uncertain': 0,
            'technical_errors': 0,
            'runs': 0,
            'blocked_sources': [],
        }
        enabled = set(settings.scan_engines)
        filters_by_source = {
            source: self._active_filters(source)
            for source in (EngineType.AUTO_RU, EngineType.AVITO)
            if source.value in enabled
        }
        total_filters = sum(len(rows) for rows in filters_by_source.values())
        overall_index = 0
        self._progress('cycle_started', total_filters=total_filters, cycle_id=self.cycle_id)

        for source in [EngineType.AUTO_RU, EngineType.AVITO]:
            if source.value not in enabled:
                continue
            filters = filters_by_source[source]
            self._progress(
                'source_started', source=source.value, filters_total=len(filters)
            )
            scan_run = ScanRun(
                cycle_id=self.cycle_id,
                started_at=started,
                source=source,
                network_profile=settings.network_profile,
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
            for filter_index, filter_entity in enumerate(filters):
                overall_index += 1
                summary['filters_scanned'] += 1
                expectations = self._all_expectations(filter_entity.id)
                preflight_rejected = []
                if self.preflight is not None:
                    eligible = []
                    for expectation in expectations:
                        checked = self.preflight['checks'].get(f'{expectation.listing_id}:{source.value}', {})
                        current_url = expectation.listing.source_auto_ru if source == EngineType.AUTO_RU else expectation.listing.source_avito
                        if (source.value in self.preflight['blocked_sources'] or checked.get('state') != 'verified'
                                or canonical_listing_key(source, checked.get('url')) != canonical_listing_key(source, current_url)):
                            preflight_rejected.append(expectation)
                            self._record_observation(scan_run, expectation.listing_id, filter_entity.id, source,
                                ObservationState.TECHNICAL_ERROR,
                                diagnostics={'error': 'seller_preflight_unresolved', 'reason': checked.get('reason', 'Нет подтверждённой сверки продавца')})
                        else:
                            eligible.append(expectation)
                    if preflight_rejected:
                        scan_run.technical_errors += 1
                        scan_run.notes = (scan_run.notes or '') + f'[{filter_entity.id}] seller preflight unresolved: {len(preflight_rejected)}\n'
                    expectations = eligible
                    if not expectations:
                        self._progress('filter_skipped', source=source.value, filter_name=filter_entity.name,
                                       overall_index=overall_index, reason='Сначала подтвердите актуальные ссылки в разделе Сверка ссылок')
                        continue
                target_keys = {
                    key
                    for expectation in expectations
                    if (
                        key := canonical_listing_key(
                            source,
                            expectation.listing.source_auto_ru
                            if source == EngineType.AUTO_RU
                            else expectation.listing.source_avito,
                        )
                    )
                }
                wait_seconds = choose_pause(
                    settings.scan_filter_pause_min_seconds,
                    settings.scan_filter_pause_max_seconds,
                )
                if wait_seconds:
                    self._progress(
                        'filter_wait',
                        source=source.value,
                        filter_name=filter_entity.name,
                        source_index=filter_index + 1,
                        source_total=len(filters),
                        overall_index=overall_index,
                        total_filters=total_filters,
                        wait_seconds=round(wait_seconds, 1),
                    )
                    time.sleep(wait_seconds)
                self._progress(
                    'filter_started',
                    source=source.value,
                    filter_id=filter_entity.id,
                    filter_name=filter_entity.name,
                    source_index=filter_index + 1,
                    source_total=len(filters),
                    overall_index=overall_index,
                    total_filters=total_filters,
                    expected=len(expectations),
                )
                if not filter_entity.raw_url:
                    self._record_technical_filter_failure(
                        scan_run,
                        filter_entity,
                        source,
                        expectations,
                        'filter has no URL',
                    )
                    self._progress(
                        'filter_finished',
                        source=source.value,
                        filter_name=filter_entity.name,
                        overall_index=overall_index,
                        status='technical_error',
                        found=0,
                        expected=len(expectations),
                        error='filter has no URL',
                    )
                    continue
                try:
                    scan_result: ScanResult = asyncio.run(
                        adapter.scan_filter(
                            filter_entity.raw_url,
                            settings.scan_pages_limit,
                            target_keys=target_keys,
                        )
                    )
                    if self._scan_lost_browser_page(scan_result):
                        retry_seconds = settings.target_closed_retry_seconds
                        self._progress(
                            'filter_retry',
                            source=source.value,
                            filter_name=filter_entity.name,
                            overall_index=overall_index,
                            retry_seconds=retry_seconds,
                            reason='управляемая вкладка Chrome была закрыта',
                        )
                        if retry_seconds:
                            time.sleep(retry_seconds)
                        scan_result = asyncio.run(
                            adapter.scan_filter(
                                filter_entity.raw_url,
                                settings.scan_pages_limit,
                                target_keys=target_keys,
                            )
                        )
                except Exception as exc:  # adapter boundary: preserve an auditable technical result
                    self._record_technical_filter_failure(
                        scan_run,
                        filter_entity,
                        source,
                        expectations,
                        f'{type(exc).__name__}: {exc}',
                    )
                    self._progress(
                        'filter_finished',
                        source=source.value,
                        filter_name=filter_entity.name,
                        overall_index=overall_index,
                        status='technical_error',
                        found=0,
                        expected=len(expectations),
                        error=f'{type(exc).__name__}: {exc}',
                    )
                    continue

                scan_run.pages_scanned += scan_result.page_count
                if not scan_result.complete:
                    self._record_technical_filter_failure(
                        scan_run,
                        filter_entity,
                        source,
                        expectations,
                        scan_result.error or 'incomplete page traversal',
                        scan_result.diagnostics,
                    )
                    self._progress(
                        'filter_finished',
                        source=source.value,
                        filter_name=filter_entity.name,
                        overall_index=overall_index,
                        status='technical_error',
                        found=0,
                        expected=len(expectations),
                        error=scan_result.error,
                    )
                    if self._scan_was_blocked(scan_result):
                        if source.value not in summary['blocked_sources']:
                            summary['blocked_sources'].append(source.value)
                        for pending_filter in filters[filter_index + 1 :]:
                            overall_index += 1
                            summary['filters_scanned'] += 1
                            self._record_technical_filter_failure(
                                scan_run,
                                pending_filter,
                                source,
                                self._all_expectations(pending_filter.id),
                                'source scan halted after marketplace challenge',
                                {'blocked_by_filter_id': filter_entity.id},
                            )
                            self._progress(
                                'filter_skipped',
                                source=source.value,
                                filter_name=pending_filter.name,
                                overall_index=overall_index,
                                reason='площадка запросила проверку пользователя',
                            )
                        break
                    continue

                if not preflight_rejected:
                    scan_run.filters_ok += 1
                expected_by_key: dict[str, VehicleFilterExpectation] = {}
                unmatchable_ids: set[str] = set()
                for expectation in expectations:
                    direct_url = (
                        expectation.listing.source_auto_ru
                        if source == EngineType.AUTO_RU
                        else expectation.listing.source_avito
                    )
                    key = canonical_listing_key(source, direct_url)
                    if key:
                        expected_by_key[key] = expectation
                    else:
                        unmatchable_ids.add(expectation.listing_id)
                        self._record_observation(
                            scan_run,
                            expectation.listing_id,
                            filter_entity.id,
                            source,
                            ObservationState.TECHNICAL_ERROR,
                            diagnostics={'error': 'active listing has no valid direct marketplace URL'},
                        )
                if unmatchable_ids:
                    scan_run.technical_errors += 1
                    scan_run.notes = (scan_run.notes or '') + (
                        f'[{filter_entity.id}] {len(unmatchable_ids)} active listing(s) '
                        'have no valid direct marketplace URL.\n'
                    )

                found_ids: set[str] = set()
                for absolute_position, hit in enumerate(scan_result.hits, start=1):
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
                        diagnostics=scan_result.diagnostics,
                        absolute_position=absolute_position,
                    )
                    expectation.listing.last_seen_at = datetime.now(UTC)
                    if scan_run.network_profile in PRODUCTION_NETWORK_PROFILES:
                        self._close_absence(scan_run, expectation.listing_id, filter_entity.id, source)
                    summary['found'] += 1

                for expectation in expectations:
                    if expectation.listing_id in found_ids or expectation.listing_id in unmatchable_ids:
                        continue
                    if scan_run.network_profile in PRODUCTION_NETWORK_PROFILES:
                        previous_misses, first_missing_at = self._previous_miss_streak(
                            expectation.listing_id,
                            filter_entity.id,
                            source,
                            scan_run.network_profile,
                        )
                        miss_count = previous_misses + 1
                        confirmed = miss_count >= settings.min_confirmed_absence_runs
                    else:
                        # A local no-VPN run proves reachability, but it must not open or
                        # extend a production business incident.
                        first_missing_at = None
                        miss_count = 1
                        confirmed = False
                    state = (
                        ObservationState.ABSENT_CONFIRMED if confirmed else ObservationState.ABSENT_UNCERTAIN
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

                self._progress(
                    'filter_finished',
                    source=source.value,
                    filter_name=filter_entity.name,
                    overall_index=overall_index,
                    status='partial' if preflight_rejected else 'ok',
                    found=len(found_ids),
                    expected=len(expectations),
                    pages=scan_result.page_count,
                    links_rejected=len(preflight_rejected),
                )

            scan_run.completed_at = datetime.now(UTC)
            scan_run.finished_at = datetime.now(UTC)
            if scan_run.filters_total == 0:
                scan_run.status = ScanRunStatus.FAILED
            elif scan_run.filters_ok == scan_run.filters_total and scan_run.technical_errors == 0:
                scan_run.status = ScanRunStatus.SUCCESS
            elif scan_run.filters_ok == 0:
                scan_run.status = ScanRunStatus.FAILED
            else:
                scan_run.status = ScanRunStatus.PARTIAL
            summary['technical_errors'] += scan_run.technical_errors
            self.db.flush()
            self._progress(
                'source_finished',
                source=source.value,
                filters_ok=scan_run.filters_ok,
                filters_total=scan_run.filters_total,
                technical_errors=scan_run.technical_errors,
            )

        self.db.commit()
        if self.preflight is not None:
            summary['links_need_review'] = sum(c['state'] != 'verified' for c in self.preflight['checks'].values())
        self._progress('cycle_finished', summary=summary)
        return summary

    @staticmethod
    def _scan_was_blocked(scan_result: ScanResult) -> bool:
        if 'http 429' in str(scan_result.error or '').lower():
            return True
        return any(
            key.endswith('_state') and value == 'blocked'
            for key, value in scan_result.diagnostics.items()
        )

    @staticmethod
    def _scan_lost_browser_page(scan_result: ScanResult) -> bool:
        return 'targetclosederror' in str(scan_result.error or '').lower()

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

    def _close_absence(self, run: ScanRun, listing_id: str, filter_id: str, source: EngineType) -> None:
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
