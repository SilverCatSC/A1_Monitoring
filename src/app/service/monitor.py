from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

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
from app.scraper.browser_session import run_browser_task, search_browser_session
from app.scraper.lazy_load import LAZY_LOAD_BLOCKED_MARKER
from app.scraper.pacing import choose_pause
from app.service.cycle_recovery import recovery_search
from app.service.locks import operation_lock


class ScanAlreadyRunning(RuntimeError):
    pass


class ScanConfigurationError(RuntimeError):
    pass


_local_scan_lock = threading.Lock()
_POSTGRES_SCAN_LOCK_KEY = 4_101_001


def validate_scan_sources() -> None:
    configured_sources = set(settings.scan_engines)
    valid_sources = {source.value for source in EngineType}
    if not configured_sources:
        raise ScanConfigurationError('scan requires at least one configured marketplace source')
    unknown_sources = sorted(configured_sources - valid_sources)
    if unknown_sources:
        raise ScanConfigurationError(
            f'unknown marketplace source(s): {", ".join(unknown_sources)}'
        )


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
        self._collected_search = None
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

    @search_browser_session()
    def collect_search_results(self) -> dict:
        """Collect once before dealer/card visits; bind vehicles only after reconciliation."""
        if settings.network_profile not in SCAN_ALLOWED_NETWORK_PROFILES:
            raise ScanConfigurationError('search collection requires an approved network profile')
        validate_scan_sources()
        with operation_lock(self.db, _POSTGRES_SCAN_LOCK_KEY, _local_scan_lock,
                            ScanAlreadyRunning, 'another scan cycle is already running'):
            if self._collected_search is not None:
                raise ScanConfigurationError('search results were already collected for this cycle')
            collected = {}
            filters_by_source = {
                source: self._active_filters(source) for source in (EngineType.AUTO_RU, EngineType.AVITO)
                if source.value in settings.scan_engines
            }
            total_filters = sum(map(len, filters_by_source.values()))
            overall_index = 0
            self._progress('search_collection_started', cycle_id=self.cycle_id, total_filters=total_filters)
            for source in (EngineType.AUTO_RU, EngineType.AVITO):
                if source.value not in settings.scan_engines:
                    continue
                filters = filters_by_source[source]
                adapter = self.auto_adapter if source == EngineType.AUTO_RU else self.avito_adapter
                blocked_by = None
                self._progress('source_started', source=source.value, filters_total=len(filters))
                for index, entity in enumerate(filters, start=1):
                    overall_index += 1
                    key = (source.value, entity.id, entity.raw_url, entity.version, settings.scan_pages_limit)
                    if blocked_by:
                        result = ScanResult(filter_id=entity.id, hits=[], page_count=0,
                                            diagnostics={'blocked_by_filter_id': blocked_by},
                                            scanned_at=datetime.now(UTC), complete=False,
                                            error='source scan halted after marketplace challenge')
                    else:
                        pause = choose_pause(settings.scan_filter_pause_min_seconds,
                                             settings.scan_filter_pause_max_seconds)
                        if pause:
                            self._progress('filter_wait', source=source.value, filter_name=entity.name,
                                           wait_seconds=round(pause, 1))
                            time.sleep(pause)
                        self._progress('filter_started', source=source.value, filter_name=entity.name,
                                       filter_id=entity.id, source_index=index, source_total=len(filters))
                        try:
                            if not entity.raw_url:
                                raise ScanConfigurationError('filter has no URL')
                            # Do not stop early using the old links: reconciliation
                            # can discover replacement ads after this collection.
                            evidence_keys = {
                                evidence_key for expectation in self._all_expectations(entity.id)
                                if (evidence_key := canonical_listing_key(source,
                                    expectation.listing.source_auto_ru if source == EngineType.AUTO_RU
                                    else expectation.listing.source_avito))
                            }
                            result = recovery_search(key, lambda adapter=adapter, entity=entity, evidence_keys=evidence_keys: run_browser_task(adapter.scan_filter(
                                entity.raw_url, settings.scan_pages_limit, target_keys=None,
                                evidence_keys=evidence_keys,
                            )))
                        except Exception as exc:
                            result = ScanResult(filter_id=entity.id, hits=[], page_count=0,
                                                diagnostics={}, scanned_at=datetime.now(UTC),
                                                complete=False, error=f'{type(exc).__name__}: {exc}')
                        if self._scan_was_blocked(result):
                            blocked_by = entity.id
                    result.diagnostics['search_collected_at'] = result.scanned_at.isoformat()
                    result.diagnostics['collection_order'] = 'search_before_active_cards'
                    collected[key] = result
                    self._progress('filter_finished', source=source.value, filter_name=entity.name,
                                   status='ok' if result.complete else 'technical_error',
                                   pages=result.page_count, cards=len(result.hits), collection_only=True,
                                   overall_index=overall_index, error=result.error)
            if self.cycle_id:
                destination = Path(settings.evidence_dir) / 'cycles' / self.cycle_id / 'search_collection.json'
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = {'cycle_id': self.cycle_id, 'results': [
                    {'source': key[0], 'filter_id': key[1], 'url': key[2], 'version': key[3],
                     'pages_limit': key[4], 'result': asdict(value)} for key, value in collected.items()
                ]}
                temporary = destination.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding='utf-8')
                temporary.replace(destination)
            self._collected_search = collected
            self._progress('search_collection_finished', cycle_id=self.cycle_id, filters=len(collected))
            return {'filters': len(collected), 'order': 'search_before_active_cards'}

    @staticmethod
    def _collected_key(entity: SearchFilter) -> tuple:
        return (entity.source.value, entity.id, entity.raw_url, entity.version, settings.scan_pages_limit)

    def _search_filter(self, adapter, entity, target_keys):
        if self._collected_search is None:
            return run_browser_task(adapter.scan_filter(
                entity.raw_url, settings.scan_pages_limit, target_keys=target_keys,
            ))
        key = self._collected_key(entity)
        if key not in self._collected_search:
            raise ScanConfigurationError('filter changed after search collection; refusing a second browser scan')
        return self._collected_search[key]

    def _saved_positions(self, source: EngineType, entity: SearchFilter) -> tuple[ScanResult | None, dict]:
        """Return this filter's precollected result and first rank per listing key.

        Never scans: without a collected result there is no positive evidence
        to retain for a seller-unresolved expectation.
        """
        if self._collected_search is None:
            return None, {}
        saved = self._collected_search.get(self._collected_key(entity))
        positions: dict[str, tuple[int, ListingHit]] = {}
        for absolute_position, hit in enumerate(saved.hits if saved else [], start=1):
            key = canonical_listing_key(source, hit.url)
            evidence = (hit.raw.get('card_evidence') or hit.raw.get('evidence')
                        or saved.diagnostics.get(f'page_{hit.page_number}_evidence'))
            if key and key not in positions and evidence and hit.page_number > 0 and hit.position > 0:
                positions[key] = (absolute_position, hit)
        return saved, positions

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
        position_provenance: dict | None = None,
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
        if position_provenance:
            raw_payload['position_provenance'] = position_provenance

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
                matched_by=((position_provenance or {}).get('matched_by', 'marketplace_listing_id')
                            if hit else None),
                raw_payload=raw_payload,
                observed_at=(datetime.fromisoformat(diagnostics['search_collected_at'])
                             if diagnostics and diagnostics.get('search_collected_at') else datetime.now(UTC)),
            )
        )

    def _previous_miss_streak(
        self,
        listing_id: str,
        filter_id: str,
        source: EngineType,
        network_profile: str,
        before: datetime | None = None,
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
        seen_times = set()
        for row in rows:
            stamp = row.observed_at.replace(tzinfo=UTC)
            if before is not None and stamp >= before.replace(tzinfo=UTC):
                continue
            if stamp in seen_times:
                continue
            seen_times.add(stamp)
            payload = row.raw_payload or {}
            if changed_at and row.observed_at.replace(tzinfo=UTC) < changed_at.replace(tzinfo=UTC):
                break
            if 'expected_listing_key' in payload and payload['expected_listing_key'] != expected_key:
                break
            if 'filter_version' in payload and payload['filter_version'] != search_filter.version:
                break
            if row.state == ObservationState.FOUND:
                if payload.get('position_provenance', {}).get('card_identity_verified') is False:
                    # A URL-only position cannot resolve a business incident.
                    continue
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

    @search_browser_session()
    def run_full_cycle(self) -> dict:
        if settings.network_profile not in SCAN_ALLOWED_NETWORK_PROFILES:
            allowed = ', '.join(sorted(SCAN_ALLOWED_NETWORK_PROFILES))
            raise ScanConfigurationError(
                f'scan requires one of [{allowed}]; current={settings.network_profile}'
            )
        validate_scan_sources()
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
            'review_required': 0,
            'technical_errors': 0,
            'runs': 0,
            'blocked_sources': [],
        }
        enabled = set(settings.scan_engines)
        if self.preflight and self.preflight.get('source_failures'):
            summary['dealer_catalogue_failures'] = [
                failure for source, failures in self.preflight['source_failures'].items()
                if source in enabled for failure in failures
            ]
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
            source_review_required = 0
            for filter_index, filter_entity in enumerate(filters):
                overall_index += 1
                summary['filters_scanned'] += 1
                expectations = self._all_expectations(filter_entity.id)
                preflight_rejected = []
                # Seller-unresolved expectations whose canonical current URL is
                # ranked in this filter's precollected complete search. Their
                # position is a search fact; catalogue and card identity stay
                # unverified, so they remain in preflight_rejected.
                located_ids: set[str] = set()
                if self.preflight is not None:
                    eligible = []
                    saved, saved_positions = self._saved_positions(source, filter_entity)
                    current_keys = {
                        expectation.listing_id: canonical_listing_key(
                            source, expectation.listing.source_auto_ru if source == EngineType.AUTO_RU
                            else expectation.listing.source_avito)
                        for expectation in expectations
                    }
                    key_counts = Counter(current_keys.values())
                    for expectation in expectations:
                        checked = self.preflight['checks'].get(f'{expectation.listing_id}:{source.value}', {})
                        current_url = expectation.listing.source_auto_ru if source == EngineType.AUTO_RU else expectation.listing.source_avito
                        current_key = current_keys[expectation.listing_id]
                        business_status = checked.get('placement_status')
                        if business_status in {'not_expected', 'inactive'}:
                            # This-cycle placement facts are not reversed by a
                            # search hit, but the conflicting hit is kept for review.
                            diagnostics = {'placement_status': business_status, 'reason': checked.get('reason')}
                            if current_key in saved_positions:
                                absolute_position, hit = saved_positions[current_key]
                                diagnostics['conflicting_saved_search_hit'] = {
                                    'url': hit.url, 'page_number': hit.page_number, 'position': hit.position,
                                    'absolute_position': absolute_position, 'search_complete': saved.complete,
                                }
                            self._record_observation(scan_run, expectation.listing_id, filter_entity.id, source,
                                ObservationState.REVIEW_REQUIRED, diagnostics=diagnostics)
                            continue
                        if (source.value in self.preflight['blocked_sources'] or checked.get('state') != 'verified'
                                or canonical_listing_key(source, checked.get('url')) != canonical_listing_key(source, current_url)):
                            preflight_rejected.append(expectation)
                            preflight_error = ('source_unavailable' if source.value in self.preflight['blocked_sources']
                                               else 'seller_preflight_unresolved')
                            located = (saved_positions.get(current_key)
                                       if saved is not None and saved.complete and current_key
                                       and key_counts[current_key] == 1
                                       and checked.get('skip_reason') != 'identity_ambiguous' else None)
                            if located is None:
                                self._record_observation(scan_run, expectation.listing_id, filter_entity.id, source,
                                    ObservationState.REVIEW_REQUIRED,
                                    diagnostics={'error': preflight_error,
                                                 'reason': checked.get('reason', 'Нет подтверждённой сверки продавца'),
                                                 'source_failures': checked.get('source_failures', [])})
                                continue
                            absolute_position, hit = located
                            located_ids.add(expectation.listing_id)
                            self._record_observation(
                                scan_run, expectation.listing_id, filter_entity.id, source,
                                ObservationState.FOUND, hit=hit,
                                diagnostics={**saved.diagnostics, 'search_complete': True,
                                             'search_pages_limit': settings.scan_pages_limit},
                                absolute_position=absolute_position,
                                position_provenance={
                                    'basis': 'saved_search_canonical_current_url',
                                    'matched_by': 'canonical_current_url',
                                    'seller_preflight_error': preflight_error,
                                    'seller_catalogue_verified': False,
                                    'card_identity_verified': False,
                                    'unique_id_read': False,
                                },
                            )
                            # Position only: no activity (last_seen_at) is inferred
                            # without a verified catalogue or card.
                            # Keep an existing incident open until identity is
                            # checked; this observation proves only URL position.
                        else:
                            eligible.append(expectation)
                    if preflight_rejected:
                        # Located positions still leave catalogue/card checks
                        # unresolved, so the run stays partial for all of them.
                        source_review_required += len(preflight_rejected)
                        summary['review_required'] += len(preflight_rejected) - len(located_ids)
                        summary['found'] += len(located_ids)
                        scan_run.notes = (scan_run.notes or '') + (
                            f'[{filter_entity.id}] seller preflight review required: {len(preflight_rejected)}'
                            + (f'; saved search positions with unverified seller identity: {len(located_ids)}'
                               if located_ids else '') + '\n')
                    expectations = eligible
                    if not expectations:
                        # A failed collected search remains a technical failure
                        # even when seller reconciliation excludes every target.
                        # Otherwise the completed cycle falsely reports zero
                        # errors for a filter that stopped during collection.
                        if self._collected_search is not None:
                            try:
                                collected_result = self._search_filter(adapter, filter_entity, set())
                            except Exception as exc:
                                collected_result = ScanResult(
                                    filter_id=filter_entity.id, hits=[], page_count=0,
                                    diagnostics={}, scanned_at=datetime.now(UTC), complete=False,
                                    error=f'{type(exc).__name__}: {exc}',
                                )
                            if not collected_result.complete:
                                scan_run.pages_scanned += collected_result.page_count
                                self._record_technical_filter_failure(
                                    scan_run, filter_entity, source, [],
                                    collected_result.error or 'incomplete page traversal',
                                    collected_result.diagnostics,
                                )
                                self._progress('filter_finished', source=source.value,
                                               filter_name=filter_entity.name,
                                               overall_index=overall_index, status='technical_error',
                                               found=0, expected=0, error=collected_result.error)
                                continue
                            if not preflight_rejected:
                                scan_run.pages_scanned += collected_result.page_count
                                scan_run.filters_ok += 1
                                self._progress('filter_finished', source=source.value, filter_name=filter_entity.name,
                                               overall_index=overall_index, status='ok', found=0, expected=0)
                                continue
                            if located_ids:
                                # Saved positions were used: count this traversal once,
                                # but the filter is not ok while seller checks are open.
                                scan_run.pages_scanned += collected_result.page_count
                                self._progress('filter_finished', source=source.value, filter_name=filter_entity.name,
                                               overall_index=overall_index, status='partial',
                                               found=len(located_ids), expected=len(located_ids),
                                               pages=collected_result.page_count,
                                               links_rejected=len(preflight_rejected) - len(located_ids),
                                               unverified_seller_positions=len(located_ids))
                                continue
                        if not preflight_rejected:
                            scan_run.filters_ok += 1
                            self._progress('filter_finished', source=source.value, filter_name=filter_entity.name,
                                           overall_index=overall_index, status='ok', found=0, expected=0)
                            continue
                        self._progress('filter_skipped', source=source.value, filter_name=filter_entity.name,
                                       overall_index=overall_index,
                                       reason=('Каталог площадки недоступен; сначала восстановите доступ'
                                               if source.value in self.preflight['blocked_sources'] else
                                               'Сначала подтвердите актуальные ссылки в разделе Сверка ссылок'))
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
                wait_seconds = 0 if self._collected_search is not None else choose_pause(
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
                    stage='matching_saved_search' if self._collected_search is not None else 'live_search',
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
                    scan_result: ScanResult = self._search_filter(adapter, filter_entity, target_keys)
                    if self._collected_search is None and self._scan_lost_browser_page(scan_result):
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
                        scan_result = run_browser_task(
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
                scan_result.diagnostics.update(search_complete=scan_result.complete,
                                               search_pages_limit=settings.scan_pages_limit)
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
                    active_catalogue_card = bool(self.preflight and self.preflight['checks'].get(
                        f'{expectation.listing_id}:{source.value}', {}).get('state') == 'verified')
                    if scan_run.network_profile in PRODUCTION_NETWORK_PROFILES and not active_catalogue_card:
                        previous_misses, first_missing_at = self._previous_miss_streak(
                            expectation.listing_id,
                            filter_entity.id,
                            source,
                            scan_run.network_profile,
                            before=scan_result.scanned_at if scan_result.diagnostics.get('recovery') else None,
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
                    found=len(found_ids) + len(located_ids),
                    expected=len(expectations) + len(located_ids),
                    pages=scan_result.page_count,
                    links_rejected=len(preflight_rejected) - len(located_ids),
                    unverified_seller_positions=len(located_ids),
                )

            scan_run.completed_at = datetime.now(UTC)
            scan_run.finished_at = datetime.now(UTC)
            if scan_run.filters_total == 0:
                scan_run.status = ScanRunStatus.FAILED
            elif (scan_run.filters_ok == scan_run.filters_total
                  and scan_run.technical_errors == 0
                  and source_review_required == 0):
                scan_run.status = ScanRunStatus.SUCCESS
            elif scan_run.filters_ok == 0 and source_review_required == 0:
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
            checks = list(self.preflight['checks'].values())
            summary['inactive_placements'] = sum(c.get('placement_status') == 'inactive' for c in checks)
            summary['not_expected_placements'] = sum(c.get('placement_status') == 'not_expected' for c in checks)
            summary['links_need_review'] = sum(c['state'] != 'verified' and
                c.get('placement_status') not in {'inactive', 'not_expected'} for c in checks)
        self._progress('cycle_finished', summary=summary)
        return summary

    @staticmethod
    def _scan_was_blocked(scan_result: ScanResult) -> bool:
        error_text = str(scan_result.error or '').lower()
        # 'lazy load blocked' is the explicit LazyLoadBlocked contract prefix:
        # the marketplace denied the continuation of the result list.
        if any(status in error_text for status in ('http 429', 'http 439')) or LAZY_LOAD_BLOCKED_MARKER in error_text:
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
