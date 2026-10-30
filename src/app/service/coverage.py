"""Shared conservative absence gate; listing a catalogue is not inspecting it."""

from app.models import EngineType
from app.scraper.base import canonical_listing_key, evidence_manifest_name


def absence_proven(report, source, url=None):
    if (report.get('catalogue_enumeration_complete', {}).get(source) is not True
            or report.get('catalogue_complete', {}).get(source) is not True
            or report.get('skipped_cards_by_source', {}).get(source, 0)
            or report.get('unverified_cards_by_source', {}).get(source, 0)
            or source in report.get('blocked_sources', [])):
        return False
    listed = report.get('catalogue_listing_urls', {}).get(source)
    if listed is not None:
        checked = {canonical_listing_key(EngineType(source), c.get('url'))
                   for c in report.get('observed_cards', []) if c.get('source') == source
                   and c.get('state') in {'active', 'removed'} and isinstance(c.get('evidence'), str)
                   and c.get('evidence_manifest') == evidence_manifest_name(c['evidence'])}
        if any(canonical_listing_key(EngineType(source), candidate) not in checked for candidate in listed):
            return False
    # Positive catalogue membership wins even if saved coverage flags conflict.
    if url:
        key = canonical_listing_key(EngineType(source), url)
        if key and any(canonical_listing_key(EngineType(source), candidate) == key
                       for candidate in report.get('catalogue_listing_urls', {}).get(source, [])):
            return False
    return True


def coverage_status(report):
    rows = []
    for source, name in [('auto_ru', 'Auto.ru'), ('avito', 'Avito')]:
        enumeration = report.get('catalogue_enumeration_complete', {})
        inspections = report.get('catalogue_complete', {})
        selected = source in enumeration or source in inspections or any(
            c.get('source') == source for c in report.get('observed_cards', []))
        complete = absence_proven(report, source)
        rows.append({'source': source, 'name': name, 'selected': selected,
                     'complete': complete,
                     'catalogue': 'полностью' if enumeration.get(source) is True else
                     'частично' if enumeration.get(source) is False else 'не подтверждён',
                     'verification': 'подтверждены' if complete else 'не полностью подтверждены',
                     'status': 'complete' if complete else 'partial' if selected else 'not_checked'})
    return rows
