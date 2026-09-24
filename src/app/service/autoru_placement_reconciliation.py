"""Read-only Auto.ru placement reconciliation from feed and opened seller cards.

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
from app.service.placement_identity import PlacementIdError, parse_placement_id


@dataclass(frozen=True)
class OpenedAutoRuCard:
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


def reconcile_autoru_placements(
    feed_rows: Iterable[Mapping[str, Any]],
    opened_cards: Iterable[OpenedAutoRuCard],
    *,
    current_urls_by_vin: Mapping[str, str] | None = None,
    first_data_row: int = 3,
    catalogue_complete: bool = False,
) -> tuple[PlacementFinding, ...]:
    """Compare ``unique_id``/``action`` with exact IDs in opened seller cards.

    ``catalogue_complete`` is recorded only in the reason for an unobserved ID.
    Even a complete catalogue cannot prove a sale or a time interval of absence.
    An ID is accepted from an active card only when its direct-card evidence was
    saved. VIN is used solely to look up the current URL already in the local
    marketing registry; a missing or duplicated VIN never triggers a guess.
    """
    rows = list(feed_rows)
    cards = list(opened_cards)
    current_urls = {str(key).strip().upper(): value for key, value in (current_urls_by_vin or {}).items()}
    findings: list[PlacementFinding] = []
    feed_by_id: dict[str, tuple[int, str, str | None]] = {}
    declared_feed_ids: set[str] = set()
    feed_id_counts = Counter(str(row.get('unique_id') or '').strip().upper() for row in rows)
    vin_counts = Counter(str(row.get('vin') or '').strip().upper() for row in rows if row.get('vin'))

    for row_number, row in enumerate(rows, first_data_row):
        raw_id = str(row.get('unique_id') or '').strip().upper()
        action = str(row.get('action') or '').strip().lower()
        vin = str(row.get('vin') or '').strip().upper() or None
        try:
            placement_id = parse_placement_id(raw_id).value
        except PlacementIdError:
            findings.append(PlacementFinding(
                'invalid_feed_id', raw_id or None, row_number, None, (),
                'Feed unique_id is not a valid 22-character placement ID',
            ))
            continue
        declared_feed_ids.add(placement_id)
        if action not in {'show', 'hide'}:
            findings.append(PlacementFinding(
                'invalid_feed_action', placement_id, row_number, None, (),
                'Feed action must be show or hide',
            ))
            continue
        if feed_id_counts[placement_id] != 1:
            findings.append(PlacementFinding(
                'duplicate_feed_id', placement_id, row_number, None, (),
                'The placement ID occurs more than once in the Auto.ru feed',
            ))
            continue
        feed_by_id[placement_id] = (row_number, action, vin)

    cards_by_id: dict[str, dict[str, str]] = defaultdict(dict)
    for opened in cards:
        inspection = opened.inspection
        if inspection.get('state') != 'active':
            continue
        if not is_marketplace_listing_url(EngineType.AUTO_RU, opened.url):
            continue
        card = inspection.get('card')
        if not isinstance(card, Mapping):
            continue
        raw_id = card.get('placement_id')
        if not isinstance(raw_id, str):
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
        key = canonical_listing_key(EngineType.AUTO_RU, opened.url)
        if key:
            cards_by_id[placement_id][key] = opened.url

    for placement_id, (row_number, action, vin) in feed_by_id.items():
        observed_urls = tuple(sorted(cards_by_id.get(placement_id, {}).values()))
        current_url = current_urls.get(vin) if vin and vin_counts[vin] == 1 else None
        if vin and vin_counts[vin] > 1:
            code, reason = 'ambiguous_feed_vin', 'Several feed rows use this VIN; vehicle link needs review'
        elif len(observed_urls) > 1:
            code, reason = 'duplicate_public_id', 'Several active Auto.ru cards declare the same placement ID'
        elif action == 'hide' and observed_urls:
            code, reason = 'hidden_but_public', 'Feed says hide, but an active seller card declares this ID'
        elif not observed_urls:
            code = 'not_verified'
            reason = (
                'ID not observed in a complete seller catalogue; this does not prove a sale'
                if catalogue_complete else
                'ID not observed in the inspected catalogue; coverage is incomplete'
            )
        elif not vin:
            code, reason = 'vehicle_anchor_missing', 'Card found, but feed VIN is absent; registry row needs review'
        elif not current_url:
            code, reason = 'current_link_missing', 'Card found, but the marketing registry has no valid current URL'
        elif canonical_listing_key(EngineType.AUTO_RU, current_url) is None:
            code, reason = 'current_link_invalid', 'Card found, but the marketing registry URL is invalid'
        elif canonical_listing_key(EngineType.AUTO_RU, current_url) == canonical_listing_key(
            EngineType.AUTO_RU, observed_urls[0]
        ):
            code, reason = 'link_current', 'The marketing registry points to the observed card for this ID'
        else:
            code, reason = 'republication_candidate', 'The same placement ID is on a different active card URL'
        findings.append(PlacementFinding(code, placement_id, row_number, current_url, observed_urls, reason))

    for placement_id, urls in cards_by_id.items():
        if placement_id not in declared_feed_ids:
            findings.append(PlacementFinding(
                'public_id_without_feed_row', placement_id, None, None,
                tuple(sorted(urls.values())), 'Active seller card declares an ID absent from the valid feed rows',
            ))
    return tuple(findings)
