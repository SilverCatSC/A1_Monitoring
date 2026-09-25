"""Apply only unambiguous, evidenced exact-ID republications before search."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import EngineType, Listing, ListingReconciliation, MonitoringCycle
from app.scraper.base import canonical_listing_key
from app.service.listings import ListingRegistryService
from app.service.placement_report import PlacementReportError, read_cycle_placement_report
from app.service.republication_review import exact_candidate_proof_url, exact_republication_candidate


class AutomaticLinkSyncService:
    def __init__(self, db: Session, *, cycle_id: str):
        self.db = db
        self.cycle_id = cycle_id

    def run(self, preflight: dict, placement: dict) -> dict:
        """Keep a review trail; never guess from model, price or visual ID aliases."""
        cycle = self.db.get(MonitoringCycle, self.cycle_id)
        if cycle is None or placement.get('status') not in {'complete', 'partial'}:
            return {'status': 'unavailable', 'updated': [], 'blocked': ['placement_report_unavailable']}
        # The proof reader intentionally accepts only a report named in the
        # durable cycle ledger, including while link synchronization is running.
        cycle.summary = {'placement_reconciliation': placement, 'status': 'preparing'}
        self.db.commit()
        try:
            report = read_cycle_placement_report(cycle, settings.evidence_dir)
        except PlacementReportError:
            return {'status': 'unavailable', 'updated': [], 'blocked': ['placement_report_invalid']}

        records = (
            self.db.query(ListingReconciliation)
            .filter_by(cycle_id=self.cycle_id, batch_id=preflight['batch_id'])
            .order_by(ListingReconciliation.checked_at, ListingReconciliation.id)
            .all()
        )
        exact = [(record, exact_republication_candidate(record)) for record in records]
        exact = [(record, candidate) for record, candidate in exact if candidate is not None]
        eligible_check_ids = {record.id for record, _ in exact}
        id_counts = Counter((record.source, candidate['placement_id']) for record, candidate in exact)
        url_counts = Counter((record.source, canonical_listing_key(record.source, candidate['url']))
                             for record, candidate in exact)
        updated = []
        blocked = [
            {'check_id': finding.get('operator_review_check_id'),
             'reason': 'non_exact_or_unresolved_republication'}
            for finding in report['findings']
            if finding.get('code') == 'republication_candidate'
            and (finding.get('id_match_basis') != 'exact'
                 or finding.get('operator_review_check_id') not in eligible_check_ids)
        ]
        for record, candidate in exact:
            listing = self.db.get(Listing, record.listing_id)
            current = (listing.source_auto_ru if record.source == EngineType.AUTO_RU else listing.source_avito)
            new_key = canonical_listing_key(record.source, candidate['url'])
            checked_at = record.checked_at.replace(tzinfo=UTC) if record.checked_at.tzinfo is None else record.checked_at
            reason = None
            if not listing.is_active or canonical_listing_key(record.source, current) != canonical_listing_key(record.source, record.url):
                reason = 'source_link_changed'
            elif (id_counts[(record.source, candidate['placement_id'])] != 1
                  or url_counts[(record.source, new_key)] != 1):
                reason = 'ambiguous_unique_id_or_url'
            elif datetime.now(UTC) - checked_at > timedelta(hours=24):
                reason = 'stale_reconciliation'
            elif exact_candidate_proof_url(self.db, record, candidate) is None:
                reason = 'evidence_not_verified'
            elif any(
                other.id != listing.id and canonical_listing_key(
                    record.source,
                    other.source_auto_ru if record.source == EngineType.AUTO_RU else other.source_avito,
                ) == new_key
                for other in self.db.query(Listing).filter(Listing.is_active.is_(True))
            ):
                reason = 'url_already_assigned'
            if reason is not None:
                blocked.append({'listing_id': record.listing_id, 'source': record.source.value, 'reason': reason})
                continue

            ListingRegistryService(self.db).update_link(
                listing.id, source=record.source, url=candidate['url'],
                actor='system:exact_unique_id',
                reason=(f'Автоматическая сверка unique_id={candidate["placement_id"]}; '
                        f'cycle={self.cycle_id}; check={record.id}; '
                        f'{candidate.get("feed_sheet")}:{candidate.get("feed_row")}'),
            )
            # A new reconciliation row describes the new URL without rewriting
            # the historical check of the old, closed card.
            new_record = ListingReconciliation(
                cycle_id=self.cycle_id, batch_id=preflight['batch_id'],
                listing_id=listing.id, source=record.source, state='verified',
                url=candidate['url'], reason='Новая карточка найдена по точному unique_id',
                candidates=[], checked_at=datetime.now(UTC),
                details={
                    'network_profile': (record.details or {}).get('network_profile'),
                    'automatic_link_sync_check_id': record.id,
                    'direct_inspection': {
                        'state': 'active', 'status_code': 'active',
                        'reason': 'Снимок новой карточки проверен по точному unique_id',
                        'evidence': candidate['evidence'],
                    },
                },
            )
            self.db.add(new_record)
            self.db.flush()
            preflight['checks'][f'{listing.id}:{record.source.value}'] = {
                'id': new_record.id, 'state': 'verified', 'url': candidate['url'],
                'reason': new_record.reason,
            }
            self.db.commit()
            updated.append({'listing_id': listing.id, 'source': record.source.value,
                            'placement_id': candidate['placement_id'],
                            'old_url': current, 'new_url': candidate['url']})
        return {'status': 'complete' if not blocked else 'partial', 'updated': updated, 'blocked': blocked}
