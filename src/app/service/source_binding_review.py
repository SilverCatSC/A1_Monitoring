"""Offline proof: VIN joins workbook rows; unique_id identifies saved adverts."""

from __future__ import annotations

import re
from collections import Counter

from app.models import EngineType
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.service.placement_identity import (
    PlacementIdError,
    parse_placement_id,
    placement_id_from_description,
    visual_ascii_placement_candidate,
)


def _rows(table):
    match = re.search(r'!([A-Z]+)(\d+):', table['range'])
    if match is None:
        raise ValueError('bounded workbook range required')
    first = int(match[2])
    return [(first + offset, row) for offset, row in enumerate(table['values'])]


def _claim(card):
    raw = card.get('placement_id_raw')
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return parse_placement_id(raw).value
    except PlacementIdError:
        return visual_ascii_placement_candidate(raw)


def validate_workbook_bindings(report, snapshot, placement_ids):
    """Return only unambiguous active workbook/card joins authorized by the owner.

    No VIN is read from a marketplace card, and price is never the identity key.
    The caller must verify screenshot hashes and registry conflicts before writing.
    """
    if not snapshot.get('authorization') or snapshot.get('cycle_id') != report.get('cycle_id'):
        raise ValueError('same-cycle owner authorization required')
    ids = [parse_placement_id(value).value for value in placement_ids]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate requested unique_id')
    marketing = _rows(snapshot['marketing'])
    feed = _rows(snapshot['feed_block']) + _rows(snapshot['feed_tail'])
    counts = Counter(str(row[0]) for key in ('new_feed_identities', 'used_feed_identities')
                     for row in snapshot[key]['values'] if row)
    bindings = []
    for value in ids:
        feed_matches = [(number, row) for number, row in feed if row and row[0] == value]
        if counts[value] != 1 or len(feed_matches) != 1:
            raise ValueError('unique_id must identify one feed row across both feeds')
        feed_row, row = feed_matches[0]
        if len(row) < 29 or placement_id_from_description(row[14]) != value:
            raise ValueError('feed label and unique_id disagree')
        vin = str(row[18] or '').strip().upper()
        if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
            raise ValueError('valid workbook VIN required')
        active = [(number, cells) for number, cells in marketing
                  if len(cells) > 7 and cells[0] == 'Актуально'
                  and str(cells[5] or '').strip().upper() == vin]
        if len(active) != 1:
            raise ValueError('VIN must identify one active marketing row')
        marketing_row, cells = active[0]
        matches = [card for card in report['observed_cards']
                   if card.get('source') == 'avito' and card.get('state') == 'active'
                   and _claim(card.get('card') or {}) == value]
        if len(matches) != 1:
            raise ValueError('unique_id must identify one active saved card')
        observed = matches[0]
        url = observed['url'].split('?')[0]
        if not is_marketplace_listing_url(EngineType.AVITO, url):
            raise ValueError('invalid saved card URL')
        key = canonical_listing_key(EngineType.AVITO, url)
        if row[2] and key != 'avito:' + str(row[2]):
            raise ValueError('feed AvitoId conflicts with card unique_id')
        marketing_url = cells[18] if len(cells) > 18 else None
        if marketing_url and canonical_listing_key(EngineType.AVITO, marketing_url) != key:
            raise ValueError('marketing URL conflicts with saved card')
        card = observed['card']
        if (float(cells[6]) != float(row[15]) or float(row[15]) != card.get('price')
                or int(cells[7]) != int(row[28]) or int(row[28]) != card.get('year')):
            raise ValueError('corroborating price or year conflicts')
        if not observed.get('evidence') or not observed.get('evidence_manifest'):
            raise ValueError('saved screenshot evidence required')
        bindings.append({
            'placement_id': value, 'marketing_vin': vin, 'marketing_row': marketing_row,
            'feed_row': feed_row, 'url': url, 'marketing_url': marketing_url,
            'model': cells[3], 'equipment': cells[4], 'price': card['price'], 'year': card['year'],
            'evidence': observed['evidence'], 'evidence_manifest': observed['evidence_manifest'],
            'raw_claim': card['placement_id_raw'],
            'basis': 'source_verified_registry_binding',
        })
    if len({b['marketing_row'] for b in bindings}) != len(bindings):
        raise ValueError('multiple unique_ids claim the same marketing row')
    return bindings
