"""Read-only marketplace placement reconciliation from feeds and opened cards.

This module consumes observations made by the approved browser runner. It does
not browse, infer vehicle identity from appearance, or change the source table.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models import EngineType
from app.scraper.base import canonical_listing_key, evidence_manifest_name, is_marketplace_listing_url
from app.service.placement_identity import (
    PlacementIdError,
    parse_placement_id,
    visual_ascii_placement_candidate,
)


@dataclass(frozen=True)
class OpenedMarketplaceCard:
    """One direct-card result returned by ``inspect_direct_link``."""

    url: str
    inspection: Mapping[str, Any]


@dataclass(frozen=True)
class PlacementFinding:
    """One reviewable outcome; no state here authorizes a link write."""

    code: str
    placement_id: str | None
    feed_row: int | None
    current_url: str | None
    observed_urls: tuple[str, ...]
    reason: str
    feed_sheet: str | None = None
    suggested_placement_id: str | None = None


OpenedAutoRuCard = OpenedMarketplaceCard


@dataclass(frozen=True)
class _FeedPlacement:
    sheet: str
    row_number: int
    raw_id: str
    action: str | None
    vin: str | None


def reconcile_autoru_placements(
    feed_rows: Iterable[Mapping[str, Any]],
    opened_cards: Iterable[OpenedMarketplaceCard],
    *,
    current_urls_by_vin: Mapping[str, str] | None = None,
    first_data_row: int = 3,
    catalogue_complete: bool = False,
) -> tuple[PlacementFinding, ...]:
    """Compare Auto.ru ``unique_id``/``action`` with opened seller cards."""
    normalized = [
        _FeedPlacement(
            'autoru-feed-all', row_number,
            str(row.get('unique_id') or '').strip().upper(),
            str(row.get('action') or '').strip().lower(),
            str(row.get('vin') or '').strip().upper() or None,
        )
        for row_number, row in enumerate(feed_rows, first_data_row)
        if any(str(value or '').strip() for value in row.values())
    ]
    return _reconcile(
        normalized, opened_cards, source=EngineType.AUTO_RU,
        current_urls_by_vin=current_urls_by_vin, catalogue_complete=catalogue_complete,
    )


def reconcile_avito_placements(
    feed_rows_by_sheet: Mapping[str, Iterable[Mapping[str, Any]]],
    opened_cards: Iterable[OpenedMarketplaceCard],
    *,
    first_data_rows: Mapping[str, int],
    current_urls_by_vin: Mapping[str, str] | None = None,
    catalogue_complete: bool = False,
) -> tuple[PlacementFinding, ...]:
    """Compare Avito ``Id`` across both feed tabs with opened seller cards.

    A feed row alone does not establish publication state. Both new and used
    tabs are required so duplicate customer IDs are detected across them.
    """
    expected_sheets = {'avito-feed-new', 'avito-feed-used'}
    if set(feed_rows_by_sheet) != expected_sheets or set(first_data_rows) != expected_sheets:
        raise ValueError('both Avito feed tabs and their first data row numbers are required')
    normalized = []
    for sheet in sorted(expected_sheets):
        first_row = first_data_rows[sheet]
        if first_row < 1:
            raise ValueError('first data row must be positive')
        normalized.extend(
            _FeedPlacement(
                sheet, row_number,
                str(row.get('Id') or '').strip().upper(),
                None,
                str(row.get('VIN') or row.get('Vin') or row.get('vin') or '').strip().upper() or None,
            )
            for row_number, row in enumerate(feed_rows_by_sheet[sheet], first_row)
            if any(str(value or '').strip() for value in row.values())
        )
    return _reconcile(
        normalized, opened_cards, source=EngineType.AVITO,
        current_urls_by_vin=current_urls_by_vin, catalogue_complete=catalogue_complete,
    )


def _reconcile(
    rows: Iterable[_FeedPlacement],
    opened_cards: Iterable[OpenedMarketplaceCard],
    *,
    source: EngineType,
    current_urls_by_vin: Mapping[str, str] | None,
    catalogue_complete: bool,
) -> tuple[PlacementFinding, ...]:
    """Compare feed identities with exact IDs on active, evidenced cards.

    ``catalogue_complete`` is recorded only in the reason for an unobserved ID.
    Even a complete catalogue cannot prove a sale or a time interval of absence.
    An ID is accepted from an active card only when its direct-card evidence was
    saved. VIN is used solely to look up the current URL already in the local
    marketing registry; a missing or duplicated VIN never triggers a guess.
    """
    rows = list(rows)
    cards = list(opened_cards)
    current_urls = {str(key).strip().upper(): value for key, value in (current_urls_by_vin or {}).items()}
    findings: list[PlacementFinding] = []
    feed_by_id: dict[str, _FeedPlacement] = {}
    declared_feed_ids: set[str] = set()
    feed_id_counts = Counter(row.raw_id for row in rows)
    vin_counts = Counter(row.vin for row in rows if row.vin)

    for row in rows:
        try:
            placement_id = parse_placement_id(row.raw_id).value
        except PlacementIdError:
            findings.append(PlacementFinding(
                'invalid_feed_id', row.raw_id or None, row.row_number, None, (),
                'Feed ID is not a valid 22-character placement ID', row.sheet,
            ))
            continue
        declared_feed_ids.add(placement_id)
        if source == EngineType.AUTO_RU and row.action not in {'show', 'hide'}:
            findings.append(PlacementFinding(
                'invalid_feed_action', placement_id, row.row_number, None, (),
                'Feed action must be show or hide', row.sheet,
            ))
            continue
        if feed_id_counts[placement_id] != 1:
            findings.append(PlacementFinding(
                'duplicate_feed_id', placement_id, row.row_number, None, (),
                'The placement ID occurs more than once across this platform feed', row.sheet,
            ))
            continue
        feed_by_id[placement_id] = row

    cards_by_id: dict[str, dict[str, str]] = defaultdict(dict)
    for opened in cards:
        inspection = opened.inspection
        if inspection.get('state') != 'active':
            continue
        if not is_marketplace_listing_url(source, opened.url):
            continue
        card = inspection.get('card')
        if not isinstance(card, Mapping):
            continue
        raw_id = card.get('placement_id')
        if not isinstance(raw_id, str):
            claim = card.get('placement_id_raw')
            if isinstance(claim, str) and claim:
                evidence = inspection.get('evidence')
                evidence_ok = (
                    isinstance(evidence, str)
                    and inspection.get('evidence_manifest') == evidence_manifest_name(evidence)
                )
                candidate = visual_ascii_placement_candidate(claim) if evidence_ok else None
                candidate_row = feed_by_id.get(candidate) if candidate else None
                if candidate_row is not None:
                    current_url = (
                        current_urls.get(candidate_row.vin)
                        if candidate_row.vin and vin_counts[candidate_row.vin] == 1 else None
                    )
                    findings.append(PlacementFinding(
                        'mixed_script_candidate', claim, candidate_row.row_number,
                        current_url, (opened.url,),
                        'Visual Cyrillic/Latin substitution suggests this feed ID; exact identity is not verified',
                        candidate_row.sheet, candidate,
                    ))
                    continue
                if not evidence_ok:
                    reason = 'The labelled card ID lacks direct-card evidence'
                elif not claim.isascii():
                    reason = 'The labelled card ID contains non-ASCII characters and cannot be matched'
                else:
                    reason = 'The labelled card ID does not satisfy the placement-ID contract'
                findings.append(PlacementFinding(
                    'invalid_card_id' if evidence_ok else 'card_evidence_missing',
                    claim, None, None, (opened.url,), reason,
                ))
            continue
        try:
            placement_id = parse_placement_id(raw_id).value
        except PlacementIdError:
            continue
        evidence = inspection.get('evidence')
        if not isinstance(evidence, str) or inspection.get('evidence_manifest') != evidence_manifest_name(evidence):
            findings.append(PlacementFinding(
                'card_evidence_missing', placement_id, None, None, (opened.url,),
                'An active card declared the ID, but its direct-card evidence is incomplete',
            ))
            continue
        key = canonical_listing_key(source, opened.url)
        if key:
            cards_by_id[placement_id][key] = opened.url

    for placement_id, row in feed_by_id.items():
        observed_urls = tuple(sorted(cards_by_id.get(placement_id, {}).values()))
        current_url = current_urls.get(row.vin) if row.vin and vin_counts[row.vin] == 1 else None
        if row.vin and vin_counts[row.vin] > 1:
            code, reason = 'ambiguous_feed_vin', 'Several feed rows use this VIN; vehicle link needs review'
        elif len(observed_urls) > 1:
            code, reason = 'duplicate_public_id', 'Several active cards declare the same placement ID'
        elif row.action == 'hide' and observed_urls:
            code, reason = 'hidden_but_public', 'Feed says hide, but an active seller card declares this ID'
        elif not observed_urls:
            code = 'not_verified'
            reason = (
                'ID not observed in a complete seller catalogue; this does not prove a sale'
                if catalogue_complete else
                'ID not observed in the inspected catalogue; coverage is incomplete'
            )
        elif not row.vin:
            code, reason = 'vehicle_anchor_missing', 'Card found, but feed VIN is absent; registry row needs review'
        elif not current_url:
            code, reason = 'current_link_missing', 'Card found, but the marketing registry has no valid current URL'
        elif not is_marketplace_listing_url(source, current_url):
            code, reason = 'current_link_invalid', 'Card found, but the marketing registry URL is invalid'
        elif canonical_listing_key(source, current_url) == canonical_listing_key(
            source, observed_urls[0]
        ):
            code, reason = 'link_current', 'The marketing registry points to the observed card for this ID'
        else:
            code, reason = 'republication_candidate', 'The same placement ID is on a different active card URL'
        findings.append(PlacementFinding(
            code, placement_id, row.row_number, current_url, observed_urls, reason, row.sheet,
        ))

    for placement_id, urls in cards_by_id.items():
        if placement_id not in declared_feed_ids:
            findings.append(PlacementFinding(
                'public_id_without_feed_row', placement_id, None, None,
                tuple(sorted(urls.values())), 'Active seller card declares an ID absent from the valid feed rows',
            ))
    return tuple(findings)
