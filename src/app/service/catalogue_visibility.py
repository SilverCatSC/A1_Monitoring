"""Positions for every observed dealer card, including links missing in marketing."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.models import EngineType, Listing
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.service.filters import CANONICAL_FILTERS, _catalog_matches, normalize_search_url
from app.service.placement_identity import PlacementIdError, parse_placement_id


def add_catalogue_visibility(report, evidence_dir, *, collection=None):
    # A dated follow-up can supply a validated collection in memory without
    # overwriting the sealed cycle's original search evidence.
    if collection is None:
        path = Path(evidence_dir) / 'cycles' / report['cycle_id'] / 'search_collection.json'
        if not path.is_file():
            return report
        try:
            collection = json.loads(path.read_text())
        except (OSError, ValueError):
            return report
    if collection.get('cycle_id') != report['cycle_id']:
        return report
    for card in report['observed_cards']:
        if (report.get('search_followup') and card.get('source') == 'avito'
                and 'original_search_positions' in card):
            # Dashboard readback also projects the historical search collection.
            # Do not overwrite a validated, dated supplement with that old gap.
            continue
        source = EngineType(card['source'])
        if not is_marketplace_listing_url(source, card['url']):
            continue
        card['safe_url'] = card['url']
        # A discovered feed ID can be shown even when marketing has no linked
        # registry row. This does not create that missing relationship.
        ids = set()
        for finding in report.get('findings', []):
            if card['url'] not in finding.get('observed_urls', []):
                continue
            value = finding.get('suggested_placement_id') or finding.get('placement_id')
            if not isinstance(value, str):
                continue
            try:
                ids.add(parse_placement_id(value).value)
            except PlacementIdError:
                pass
        card['unique_id'] = next(iter(ids)) if len(ids) == 1 else None
        key = canonical_listing_key(source, card['url'])
        probe = Listing(source_avito=card['url'] if source == EngineType.AVITO else None,
                        source_auto_ru=card['url'] if source == EngineType.AUTO_RU else None)
        positions = []
        for entry in collection.get('results', []):
            if entry['source'] != source.value:
                continue
            result = entry['result']
            definition = next((d for d in CANONICAL_FILTERS if d.source == source
                               and normalize_search_url(d.url) == normalize_search_url(entry['url'])), None)
            matches = [h for h in result['hits'] if canonical_listing_key(source, h['url']) == key]
            relevant, relevance_reason = (_catalog_matches(probe, definition) if definition is not None
                                          else (False, 'unknown_filter'))
            condition_unknown = relevance_reason == 'condition_unknown'
            if not matches and not relevant and not condition_unknown:
                continue
            label = 'Поиск не завершён'
            if matches:
                first = matches[0]
                label = f"Стр. {first['page_number']}, место {first['position']}"
            elif result['complete'] and card['state'] == 'active':
                # Family/condition matching cannot establish the generation,
                # configuration or engine constraints of a detailed filter.
                # A hit is direct positive evidence; a miss is not 5+ until
                # those extra constraints are checked against the actual car.
                filter_url = urlsplit(definition.url)
                narrow_filter = ('/group/' in filter_url.path
                                 or bool(parse_qs(filter_url.query).get('catalog_filter')))
                label = ('Применимость фильтра не подтверждена' if narrow_filter or condition_unknown
                         else f"{entry['pages_limit']}+")
            positions.append({'filter_id': entry['filter_id'],
                              'filter_name': definition.name if definition else entry['url'],
                              'label': label, 'complete': result['complete'],
                              'page': matches[0]['page_number'] if matches else None,
                              'position': matches[0]['position'] if matches else None})
        card['search_positions'] = positions
    return report
