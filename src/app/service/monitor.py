from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
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


class MonitorService:
    def __init__(self, db: Session):
        self.db = db
        self.auto_adapter = AutoRuAdapter(request_timeout=settings.request_timeout_seconds)
        self.avito_adapter = AvitoAdapter(request_timeout=settings.request_timeout_seconds)

    def _active_filters(self, source: EngineType):
        return (
            self.db.query(SearchFilter)
            .filter(SearchFilter.source == source, SearchFilter.active.is_(True))
            .all()
        )

    def _all_expectations(self, filter_id: str):
        return (
            self.db.query(VehicleFilterExpectation)
            .filter(VehicleFilterExpectation.filter_id == filter_id)
            .all()
        )

    def _record_observation(self, run, listing_id: str, filter_id: str, hit, source: EngineType, found: bool):
        state = ObservationState.FOUND if found else ObservationState.ABSENT_CONFIRMED
        self.db.add(
            ListingObservation(
                run_id=run.id,
                listing_id=listing_id,
                filter_id=filter_id,
                source=source,
                page_number=hit.page_number if found else 0,
                position_in_page=hit.position if found else 0,
                found=found,
                state=state,
                listing_url=hit.url if found else None,
                title=hit.title if found else None,
                price_hint=hit.price,
                matched_by='url_contains' if found else None,
                raw_payload=hit.raw,
            )
        )

    def run_full_cycle(self) -> dict[str, int]:
        started = datetime.utcnow()
        summary = {'filters_scanned': 0, 'found': 0, 'missed': 0, 'runs': 0}

        for source in [EngineType.AUTO_RU, EngineType.AVITO]:
            scan_run = ScanRun(
                started_at=started,
                source=source,
                status=ScanRunStatus.IN_PROGRESS,
                filters_total=len(self._active_filters(source)),
                notes=None,
            )
            self.db.add(scan_run)
            self.db.flush()
            summary['runs'] += 1

            filters = self._active_filters(source)
            adapter = self.auto_adapter if source == EngineType.AUTO_RU else self.avito_adapter

            for filter_entity in filters:
                summary['filters_scanned'] += 1
                if not filter_entity.raw_url:
                    continue
                try:
                    scan_result = asyncio.get_event_loop().run_until_complete(
                        adapter.scan_filter(filter_entity.raw_url or '', settings.scan_pages_limit)
                    )
                except Exception as exc:  # broad for safety in first phase
                    scan_run.technical_errors += 1
                    run_note = str(exc)
                    scan_run.notes = (scan_run.notes or '') + f'[{filter_entity.id}] {run_note}\n'
                    continue

                scan_run.pages_scanned = max(scan_run.pages_scanned, scan_result.page_count)
                scan_run.filters_ok += 1
                expected_links = self._all_expectations(filter_entity.id)
                expected_by_id = {exp.listing_id: exp for exp in expected_links}
                found_ids = set()

                for hit in scan_result.hits:
                    listing = self.db.query(Listing).filter(Listing.source_auto_ru == hit.url).one_or_none()
                    if listing is None:
                        listing = self.db.query(Listing).filter(Listing.source_avito == hit.url).one_or_none()
                    if listing is None:
                        listing = self.db.query(Listing).filter(Listing.direct_url == hit.url).one_or_none()
                    if listing is None:
                        continue
                    if listing.id not in expected_by_id:
                        continue
                    found_ids.add(listing.id)
                    self._record_observation(scan_run, listing.id, filter_entity.id, hit, source, True)
                    listing.last_seen_at = datetime.utcnow()
                    summary['found'] += 1

                for expectation in expected_links:
                    if expectation.listing_id not in found_ids:
                        self._record_observation(
                            scan_run,
                            expectation.listing_id,
                            filter_entity.id,
                            type(
                                'placeholder',
                                (),
                                {
                                    'page_number': 0,
                                    'position': 0,
                                    'price': None,
                                    'title': None,
                                    'url': None,
                                    'raw': {},
                                },
                            ),
                            source,
                            False,
                        )
                        summary['missed'] += 1
                        self._upsert_absence(scan_run, expectation.listing_id, filter_entity.id, source)
                    else:
                        self._close_absence(scan_run, expectation.listing_id, filter_entity.id, source)

            scan_run.completed_at = datetime.utcnow()
            scan_run.finished_at = datetime.utcnow()
            scan_run.status = (
                ScanRunStatus.SUCCESS if scan_run.technical_errors == 0 else ScanRunStatus.PARTIAL
            )
            self.db.flush()

        self.db.commit()
        return summary

    def _upsert_absence(self, run: ScanRun, listing_id: str, filter_id: str, source: EngineType) -> None:
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
                    consecutive_misses=1,
                    observed_count=1,
                    last_missing_run_id=run.id,
                    started_at=run.started_at,
                )
            )
            return

        open_episode.consecutive_misses += 1
        open_episode.observed_count += 1
        open_episode.last_missing_run_id = run.id
        if open_episode.consecutive_misses >= settings.min_confirmed_absence_runs:
            open_episode.notes = 'confirmed_absence_candidate'

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
