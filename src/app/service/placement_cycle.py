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
from app.service.analytics import money
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
        # Reuse a current, evidenced card opened for ID comparison as the
        # direct-card check for this cycle. Never reuse an unknown/blocked card.
        opened_inspections = {}
        opened_counts = Counter()
        for source, cards in opened.items():
            for card in cards:
                key = (source, canonical_listing_key(source, card.url))
                opened_counts[key] += 1
                opened_inspections[key] = card.inspection
        reused_direct_cards = 0
        for record in previous:
            if record.state != 'verified' or (record.details or {}).get('direct_inspection'):
                continue
            key = (record.source, canonical_listing_key(record.source, record.url))
            inspection = opened_inspections.get(key)
            if (opened_counts[key] != 1 or not isinstance(inspection, dict)
                    or inspection.get('state') != 'active'
                    or not isinstance(inspection.get('evidence'), str)
                    or inspection.get('evidence_manifest')
                    != evidence_manifest_name(inspection['evidence'])):
                continue
            record.details = {**(record.details or {}), 'direct_inspection': inspection}
            reused_direct_cards += 1
        candidate_updates = self._operator_candidate_updates(
            findings, snapshot, opened, candidates, listings, previous,
        )
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
            'reused_direct_cards': reused_direct_cards,
            'blocked_sources': sorted(blocked),
            'observed_cards': [
                {
                    'source': source.value,
                    'url': card.url,
                    'state': card.inspection.get('state'),
                    'evidence': card.inspection.get('evidence'),
                    'evidence_manifest': card.inspection.get('evidence_manifest'),
                }
                for source in (EngineType.AUTO_RU, EngineType.AVITO)
                for card in opened[source]
            ],
            'findings': findings,
        }
        report_path = self._write_report(report)
        for record, updated in candidate_updates:
            record.candidates = updated
        if candidate_updates or reused_direct_cards:
            self.db.commit()
        self.progress({'event': 'placement_reconciliation_finished', 'findings': len(findings)})
        return {
            'status': 'complete' if all(coverage.values()) else 'partial',
            'findings': len(findings),
            'codes': dict(Counter(item['code'] for item in findings)),
            'report_path': report_path,
        }

    def _operator_candidate_updates(self, findings, snapshot, opened, candidates, listings, records):
        """Queue unambiguous ID findings for a human; never update registry links."""
        source_for_sheet = {
            'autoru-feed-all': EngineType.AUTO_RU,
            'avito-feed-new': EngineType.AVITO,
            'avito-feed-used': EngineType.AVITO,
        }
        feed_vin_counts = Counter()
        for sheet, rows in snapshot.rows_by_sheet.items():
            source = source_for_sheet.get(sheet)
            if source is not None:
                feed_vin_counts.update(
                    (source, str(row.get('vin') or row.get('VIN') or row.get('Vin') or '').strip().upper())
                    for row in rows if str(row.get('vin') or row.get('VIN') or row.get('Vin') or '').strip()
                )
        listings_by_vin = {}
        for listing in listings:
            vin = str(listing.vin or '').strip().upper()
            if vin:
                listings_by_vin.setdefault(vin, []).append(listing)
        records_by_key = {}
        for record in records:
            records_by_key.setdefault((record.source, record.listing_id), []).append(record)
        candidates_by_key = {}
        for candidate in candidates:
            key = (candidate.source, canonical_listing_key(candidate.source, candidate.listing_url))
            candidates_by_key.setdefault(key, []).append(candidate)
        opened_by_key = {}
        for source, cards in opened.items():
            for card in cards:
                key = (source, canonical_listing_key(source, card.url))
                opened_by_key.setdefault(key, []).append(card)
        updates = {}
        for finding in findings:
            if (finding['code'] != 'republication_candidate'
                    or finding['id_match_basis'] not in {'exact', 'visual_alias'}
                    or len(finding['observed_urls']) != 1):
                continue
            sheet = finding['feed_sheet']
            if sheet not in source_for_sheet or not isinstance(finding['feed_row'], int):
                continue
            source = source_for_sheet.get(sheet)
            row_index = finding['feed_row'] - snapshot.first_data_rows[sheet]
            rows = snapshot.rows_by_sheet[sheet]
            if row_index < 0 or row_index >= len(rows):
                continue
            row = rows[row_index]
            vin = str(row.get('vin') or row.get('VIN') or row.get('Vin') or '').strip().upper()
            if (not vin or feed_vin_counts[(source, vin)] != 1
                    or len(listings_by_vin.get(vin, [])) != 1):
                continue
            listing = listings_by_vin[vin][0]
            matching_records = records_by_key.get((source, listing.id), [])
            if len(matching_records) != 1:
                continue
            record = matching_records[0]
            if (record.state not in {'review_required', 'removed', 'missing_link'}
                    or canonical_listing_key(source, record.url) != canonical_listing_key(source, finding['current_url'])):
                continue
            new_url = finding['observed_urls'][0]
            new_key = canonical_listing_key(source, new_url)
            matching_candidates = candidates_by_key.get((source, new_key), [])
            matching_cards = opened_by_key.get((source, new_key), [])
            if len(matching_candidates) != 1 or len(matching_cards) != 1:
                continue
            candidate, card = matching_candidates[0], matching_cards[0]
            if card.inspection.get('state') != 'active':
                continue
            evidence = card.inspection.get('evidence')
            if (not isinstance(evidence, str)
                    or card.inspection.get('evidence_manifest') != evidence_manifest_name(evidence)):
                continue
            if any(
                other.id != listing.id and canonical_listing_key(
                    source, other.source_auto_ru if source == EngineType.AUTO_RU else other.source_avito
                ) == new_key
                for other in listings
            ):
                continue
            basis = (
                'ID в карточке и фиде совпал; VIN связывает фид с записью. '
                'Проверьте, что это тот же автомобиль перед подтверждением.'
                if finding['id_match_basis'] == 'exact' else
                'ID в карточке сопоставлен по известному кириллическому двойнику; '
                'VIN связывает фид с записью. Нужна ручная проверка автомобиля и ошибки ID.'
            )
            entry = {
                'id': candidate.id, 'url': new_url,
                'title': candidate.title or 'Карточка с ID из фида',
                'price': money(candidate.price_hint), 'basis': basis,
                'placement_id': finding['placement_id'],
                'id_match_basis': finding['id_match_basis'],
                'feed_sheet': sheet, 'feed_row': finding['feed_row'],
                'evidence': evidence,
            }
            current = updates.get(record.id, (record, list(record.candidates or [])))[1]
            current = [item for item in current if canonical_listing_key(source, item.get('url')) != new_key]
            current.append(entry)
            updates[record.id] = (record, current)
            finding['operator_review_check_id'] = record.id
        return list(updates.values())

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
