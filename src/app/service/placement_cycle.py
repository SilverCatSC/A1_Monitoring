"""Bounded, read-only ID reconciliation for a completed seller-catalogue pass."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models import DealerDiscoveryRun, DealerListingCandidate, EngineType, Listing, ListingReconciliation
from app.scraper.base import canonical_listing_key, evidence_manifest_name, is_marketplace_listing_url
from app.scraper.seller import inspect_direct_link
from app.service.marketplace_placement_reconciliation import (
    OpenedMarketplaceCard,
    reconcile_autoru_placements,
    reconcile_avito_placements,
)
from app.service.placement_feed_snapshot import PlacementFeedError, read_placement_feed_snapshot
from app.service.placement_identity import (
    PlacementIdError,
    parse_placement_id,
    visual_ascii_placement_candidate,
)


def _card_has_usable_id_claim(inspection: dict) -> bool:
    card = inspection.get('card')
    if not isinstance(card, Mapping):
        return False
    exact = card.get('placement_id')
    if isinstance(exact, str):
        try:
            parse_placement_id(exact)
            return True
        except PlacementIdError:
            pass
    raw = card.get('placement_id_raw')
    return isinstance(raw, str) and visual_ascii_placement_candidate(raw) is not None


class PlacementCycleService:
    def __init__(self, db, *, inspector=None, progress_callback=None, cycle_id: str):
        self.db = db
        self.inspector = inspector or inspect_direct_link
        self.progress = progress_callback or (lambda _event: None)
        self.cycle_id = cycle_id

    def run(self, preflight: dict, source_url: str | None) -> dict:
        """Inspect only this cycle's discovered cards; never edit registry URLs."""
        selected_sources = {
            source for source in (EngineType.AUTO_RU, EngineType.AVITO)
            if source.value in settings.scan_engines
        }
        if not selected_sources:
            return {'status': 'unavailable', 'reason': 'no selected marketplace', 'findings': 0}
        try:
            snapshot = read_placement_feed_snapshot(source_url)
        except PlacementFeedError as exc:
            return {'status': 'unavailable', 'reason': str(exc), 'findings': 0}

        run_ids = set(preflight.get('discovery', {}).get('run_ids', []))
        discovery_runs = (
            self.db.query(DealerDiscoveryRun)
            .filter(DealerDiscoveryRun.id.in_(run_ids))
            .all()
        ) if run_ids else []
        if not discovery_runs:
            return {'status': 'unavailable', 'reason': 'no current-cycle dealer catalogue', 'findings': 0}
        source_by_run_id = {run.id: run.source for run in discovery_runs}
        candidates = (
            self.db.query(DealerListingCandidate)
            .filter(DealerListingCandidate.active.is_(True))
            .order_by(DealerListingCandidate.source, DealerListingCandidate.external_key)
            .all()
        )
        candidates = [
            item for item in candidates
            if (item.raw_payload or {}).get('discovery_run_id') in run_ids
            and source_by_run_id.get((item.raw_payload or {}).get('discovery_run_id')) == item.source
            and item.network_profile == settings.network_profile
            and item.source in selected_sources
            and is_marketplace_listing_url(item.source, item.listing_url)
        ]
        previous = (
            self.db.query(ListingReconciliation)
            .filter(ListingReconciliation.batch_id == preflight['batch_id'])
            .all()
        )
        cached = {}
        for record in previous:
            inspection = (record.details or {}).get('direct_inspection')
            key = canonical_listing_key(record.source, record.url)
            if key and isinstance(inspection, dict):
                cached[(record.source, key)] = inspection

        opened = {EngineType.AUTO_RU: [], EngineType.AVITO: []}
        skipped = Counter()
        unverified = Counter()
        blocked = set(preflight.get('blocked_sources', []))
        new_checks = 0
        for candidate in candidates:
            source = candidate.source
            if source.value in blocked:
                skipped[source.value] += 1
                continue
            key = canonical_listing_key(source, candidate.listing_url)
            inspection = cached.get((source, key))
            if inspection is None:
                if new_checks >= settings.seller_identity_checks_limit:
                    skipped[source.value] += 1
                    continue
                new_checks += 1
                inspection = asyncio.run(self.inspector(source, candidate.listing_url, self.progress))
            if inspection.get('state') == 'blocked':
                blocked.add(source.value)
            evidence = inspection.get('evidence')
            if (inspection.get('state') != 'active' or not isinstance(evidence, str)
                    or inspection.get('evidence_manifest') != evidence_manifest_name(evidence)
                    or not _card_has_usable_id_claim(inspection)):
                unverified[source.value] += 1
            opened[source].append(OpenedMarketplaceCard(candidate.listing_url, inspection))

        listings = self.db.query(Listing).filter(Listing.is_active.is_(True)).all()
        vin_counts = Counter(str(item.vin or '').strip().upper() for item in listings if item.vin)
        urls_by_source = {}
        for source in (EngineType.AUTO_RU, EngineType.AVITO):
            url_counts = Counter(
                canonical_listing_key(
                    source, item.source_auto_ru if source == EngineType.AUTO_RU else item.source_avito
                ) for item in listings
            )
            urls_by_source[source] = {
                str(item.vin).strip().upper(): (
                    item.source_auto_ru if source == EngineType.AUTO_RU else item.source_avito
                )
                for item in listings
                if item.vin and vin_counts[str(item.vin).strip().upper()] == 1
                and url_counts[canonical_listing_key(
                    source, item.source_auto_ru if source == EngineType.AUTO_RU else item.source_avito
                )] == 1
            }
        coverage = {
            source: (
                any(run.source == source for run in discovery_runs)
                and all(run.complete for run in discovery_runs if run.source == source)
                and not skipped[source.value]
                and not unverified[source.value]
                and source.value not in blocked
            )
            for source in selected_sources
        }
        auto = reconcile_autoru_placements(
            snapshot.rows_by_sheet['autoru-feed-all'], opened[EngineType.AUTO_RU],
            first_data_row=snapshot.first_data_rows['autoru-feed-all'],
            current_urls_by_vin=urls_by_source[EngineType.AUTO_RU],
            catalogue_complete=coverage[EngineType.AUTO_RU],
        ) if EngineType.AUTO_RU in selected_sources else ()
        avito = reconcile_avito_placements(
            {sheet: snapshot.rows_by_sheet[sheet] for sheet in ('avito-feed-new', 'avito-feed-used')},
            opened[EngineType.AVITO],
            first_data_rows={sheet: snapshot.first_data_rows[sheet]
                             for sheet in ('avito-feed-new', 'avito-feed-used')},
            current_urls_by_vin=urls_by_source[EngineType.AVITO],
            catalogue_complete=coverage[EngineType.AVITO],
        ) if EngineType.AVITO in selected_sources else ()
        findings = [asdict(item) for item in (*auto, *avito)]
        report = {
            'schema_version': 1,
            'cycle_id': self.cycle_id,
            'created_at_utc': datetime.now(UTC).isoformat(),
            'feed_sha256_by_sheet': snapshot.sha256_by_sheet,
            'feed_rows_by_sheet': {sheet: len(rows) for sheet, rows in snapshot.rows_by_sheet.items()},
            'catalogue_complete': {source.value: complete for source, complete in coverage.items()},
            'new_card_checks': new_checks,
            'skipped_cards_by_source': dict(skipped),
            'unverified_cards_by_source': dict(unverified),
            'blocked_sources': sorted(blocked),
            'findings': findings,
        }
        report_path = self._write_report(report)
        self.progress({'event': 'placement_reconciliation_finished', 'findings': len(findings)})
        return {
            'status': 'complete' if all(coverage.values()) else 'partial',
            'findings': len(findings),
            'codes': dict(Counter(item['code'] for item in findings)),
            'report_path': report_path,
        }

    def _write_report(self, report: dict) -> str:
        if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', self.cycle_id):
            raise ValueError('invalid cycle ID for placement artifact')
        relative = Path('cycles') / self.cycle_id / 'placement_reconciliation.json'
        absolute = Path(settings.evidence_dir).resolve() / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(absolute, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        return relative.as_posix()
