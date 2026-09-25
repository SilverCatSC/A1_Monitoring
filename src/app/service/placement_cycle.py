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
from app.models import (
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    Listing,
    ListingPlacementIdentity,
    ListingReconciliation,
)
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

        feed_counts = Counter()
        active_exact_feed_ids = set()
        avito_platform_ids = {}
        avito_platform_counts = Counter()
        for sheet, rows in snapshot.rows_by_sheet.items():
            source = EngineType.AUTO_RU if sheet == 'autoru-feed-all' else EngineType.AVITO
            for row in rows:
                raw = row.get('unique_id') if source == EngineType.AUTO_RU else row.get('Id')
                try:
                    placement_id = parse_placement_id(str(raw or '')).value
                except PlacementIdError:
                    placement_id = visual_ascii_placement_candidate(str(raw or ''))
                else:
                    if source == EngineType.AVITO or str(row.get('action') or '').strip().lower() == 'show':
                        active_exact_feed_ids.add((source, placement_id))
                if placement_id:
                    feed_counts[source, placement_id] += 1
                    if source == EngineType.AVITO:
                        platform_id = str(row.get('AvitoId') or '').strip()
                        if re.fullmatch(r'[0-9]{5,}', platform_id):
                            avito_platform_counts[platform_id] += 1
                            avito_platform_ids[platform_id] = placement_id
        listings = self.db.query(Listing).filter(Listing.is_active.is_(True)).all()
        listing_by_id = {item.id: item for item in listings}
        url_counts = Counter(
            (source, canonical_listing_key(source, (
                item.source_auto_ru if source == EngineType.AUTO_RU else item.source_avito
            )))
            for item in listings
            for source in (EngineType.AUTO_RU, EngineType.AVITO)
        )
        current_identity_keys = set()
        for identity in self.db.query(ListingPlacementIdentity).all():
            if ((identity.source, identity.placement_id) not in active_exact_feed_ids
                    or feed_counts[identity.source, identity.placement_id] != 1):
                continue
            listing = listing_by_id.get(identity.listing_id)
            if listing is None:
                continue
            current_url = (
                listing.source_auto_ru if identity.source == EngineType.AUTO_RU
                else listing.source_avito
            )
            current_key = canonical_listing_key(identity.source, current_url)
            if current_key and identity.source == EngineType.AVITO:
                platform_id = current_key.removeprefix('avito:')
                if (avito_platform_counts[platform_id] > 1
                        or (platform_id in avito_platform_ids
                            and avito_platform_ids[platform_id] != identity.placement_id)):
                    continue
            if current_key and url_counts[identity.source, current_key] == 1:
                current_identity_keys.add((identity.source, current_key))

        opened = {EngineType.AUTO_RU: [], EngineType.AVITO: []}
        skipped = Counter()
        deferred_current_id = Counter()
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
                # This URL is already in today's dealer catalogue and has a
                # persisted exact-ID binding. It cannot be a changed link.
                # Defer its content/price check to the post-search direct stage.
                if (source, key) in current_identity_keys:
                    deferred_current_id[source.value] += 1
                    continue
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

        urls_by_source, new_identities = self._identity_urls(snapshot, opened, listings)
        coverage = {
            source: (
                any(run.source == source for run in discovery_runs)
                and all(run.complete for run in discovery_runs if run.source == source)
                and not skipped[source.value]
                and not deferred_current_id[source.value]
                and not unverified[source.value]
                and source.value not in blocked
            )
            for source in selected_sources
        }
        auto = reconcile_autoru_placements(
            snapshot.rows_by_sheet['autoru-feed-all'], opened[EngineType.AUTO_RU],
            first_data_row=snapshot.first_data_rows['autoru-feed-all'],
            current_urls_by_placement_id=urls_by_source[EngineType.AUTO_RU],
            catalogue_complete=coverage[EngineType.AUTO_RU],
        ) if EngineType.AUTO_RU in selected_sources else ()
        avito = reconcile_avito_placements(
            {sheet: snapshot.rows_by_sheet[sheet] for sheet in ('avito-feed-new', 'avito-feed-used')},
            opened[EngineType.AVITO],
            first_data_rows={sheet: snapshot.first_data_rows[sheet]
                             for sheet in ('avito-feed-new', 'avito-feed-used')},
            current_urls_by_placement_id=urls_by_source[EngineType.AVITO],
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
            'deferred_current_id_cards_by_source': dict(deferred_current_id),
            'unverified_cards_by_source': dict(unverified),
            'reused_direct_cards': reused_direct_cards,
            'new_verified_identities': new_identities,
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
        if candidate_updates or reused_direct_cards or new_identities:
            self.db.commit()
        self.progress({'event': 'placement_reconciliation_finished', 'findings': len(findings)})
        return {
            'status': 'complete' if all(coverage.values()) else 'partial',
            'findings': len(findings),
            'codes': dict(Counter(item['code'] for item in findings)),
            'report_path': report_path,
        }

    def _identity_urls(self, snapshot, opened, listings):
        """Bootstrap only from an exact current URL/card/feed triple, never VIN."""
        rows_by_source = {
            EngineType.AUTO_RU: snapshot.rows_by_sheet['autoru-feed-all'],
            EngineType.AVITO: (
                *snapshot.rows_by_sheet['avito-feed-new'],
                *snapshot.rows_by_sheet['avito-feed-used'],
            ),
        }
        feed_counts = Counter()
        exact_feed_ids = set()
        active_feed_ids = set()
        avito_platform_ids = {}
        avito_platform_counts = Counter()
        for source, rows in rows_by_source.items():
            for row in rows:
                raw = row.get('unique_id') if source == EngineType.AUTO_RU else row.get('Id')
                try:
                    normalized_id = parse_placement_id(str(raw or '')).value
                except PlacementIdError:
                    normalized_id = visual_ascii_placement_candidate(str(raw or ''))
                    if normalized_id is None:
                        continue
                else:
                    exact_feed_ids.add((source, normalized_id))
                    if source == EngineType.AVITO or str(row.get('action') or '').strip().lower() == 'show':
                        active_feed_ids.add((source, normalized_id))
                feed_counts[source, normalized_id] += 1
                if source == EngineType.AVITO:
                    platform_id = str(row.get('AvitoId') or '').strip()
                    if re.fullmatch(r'[0-9]{5,}', platform_id):
                        avito_platform_counts[platform_id] += 1
                        avito_platform_ids[platform_id] = normalized_id
        listing_by_id = {item.id: item for item in listings}
        listing_urls = {}
        url_counts = Counter()
        for source in (EngineType.AUTO_RU, EngineType.AVITO):
            for item in listings:
                url = item.source_auto_ru if source == EngineType.AUTO_RU else item.source_avito
                key = canonical_listing_key(source, url)
                if key:
                    listing_urls[source, key] = item
                    url_counts[source, key] += 1
        identities = self.db.query(ListingPlacementIdentity).all()
        bound_by_id = {(item.source, item.placement_id): item for item in identities}
        bound_by_listing = {(item.source, item.listing_id): item for item in identities}
        card_counts = Counter()
        for source, cards in opened.items():
            for card in cards:
                value = (card.inspection.get('card') or {}).get('placement_id')
                if card.inspection.get('state') == 'active' and isinstance(value, str):
                    try:
                        card_counts[source, parse_placement_id(value).value] += 1
                    except PlacementIdError:
                        pass
        added = 0
        for source, cards in opened.items():
            for card in cards:
                inspection = card.inspection
                value = (inspection.get('card') or {}).get('placement_id')
                if not isinstance(value, str):
                    continue
                try:
                    placement_id = parse_placement_id(value).value
                except PlacementIdError:
                    continue
                evidence = inspection.get('evidence')
                key = canonical_listing_key(source, card.url)
                listing = listing_urls.get((source, key))
                if (inspection.get('state') != 'active' or not isinstance(evidence, str)
                        or inspection.get('evidence_manifest') != evidence_manifest_name(evidence)
                        or not key or url_counts[source, key] != 1 or listing is None
                        or card_counts[source, placement_id] != 1
                        or feed_counts[source, placement_id] != 1
                        or (source, placement_id) not in exact_feed_ids
                        or (source, placement_id) not in active_feed_ids
                        or (source, placement_id) in bound_by_id
                        or (source, listing.id) in bound_by_listing):
                    continue
                if source == EngineType.AVITO:
                    platform_id = key.removeprefix('avito:')
                    if (avito_platform_counts[platform_id] > 1
                            or (platform_id in avito_platform_ids
                                and avito_platform_ids[platform_id] != placement_id)):
                        continue
                identity = ListingPlacementIdentity(
                    source=source, listing_id=listing.id, placement_id=placement_id,
                    evidence=evidence,
                )
                self.db.add(identity)
                bound_by_id[source, placement_id] = identity
                bound_by_listing[source, listing.id] = identity
                added += 1
        urls = {EngineType.AUTO_RU: {}, EngineType.AVITO: {}}
        for identity in bound_by_id.values():
            listing = listing_by_id.get(identity.listing_id)
            if listing is None:
                continue
            url = listing.source_auto_ru if identity.source == EngineType.AUTO_RU else listing.source_avito
            key = canonical_listing_key(identity.source, url)
            if key and url_counts[identity.source, key] == 1:
                urls[identity.source][identity.placement_id] = url
        return urls, added

    def _operator_candidate_updates(self, findings, snapshot, opened, candidates, listings, records):
        """Queue unambiguous ID findings for a human; never update registry links."""
        source_for_sheet = {
            'autoru-feed-all': EngineType.AUTO_RU,
            'avito-feed-new': EngineType.AVITO,
            'avito-feed-used': EngineType.AVITO,
        }
        identities = {
            (item.source, item.placement_id): item.listing_id
            for item in self.db.query(ListingPlacementIdentity).all()
        }
        listings_by_id = {item.id: item for item in listings}
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
            placement_id = finding['placement_id']
            listing_id = identities.get((source, placement_id))
            listing = listings_by_id.get(listing_id)
            if listing is None:
                continue
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
                'ID в карточке и фиде совпал с ранее подтверждённой связью unique_id и автомобиля.'
                if finding['id_match_basis'] == 'exact' else
                'ID в карточке сопоставлен по известному кириллическому двойнику; '
                'связь unique_id и автомобиля подтверждена ранее. Нужна ручная проверка ошибки ID.'
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
