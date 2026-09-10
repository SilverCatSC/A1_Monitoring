from datetime import UTC, datetime

from app.models import AbsenceEpisode, EngineType, ListingChangeEvent, ListingLinkEvent
from app.scraper.base import canonical_listing_key

AUDITED_FIELDS = ('brand', 'model', 'year', 'price_hint', 'is_active', 'source_auto_ru', 'source_avito')


def registry_values(listing):
    return {field: getattr(listing, field) for field in AUDITED_FIELDS}


def close_replaced_episodes(db, listing_id, source, old_url, new_url):
    if canonical_listing_key(source, old_url) == canonical_listing_key(source, new_url):
        return
    for episode in db.query(AbsenceEpisode).filter_by(listing_id=listing_id, source=source, open=True):
        episode.open = False
        episode.ended_at = datetime.now(UTC)
        episode.notes = (episode.notes or '') + ' Registry URL replaced; old episode ended without proof of visibility.'


def record_registry_change(db, listing, previous, snapshot_id):
    values = registry_values(listing)
    changes = {
        key: {'old': previous.get(key) if previous else None, 'new': value}
        for key, value in values.items()
        if previous is None or previous.get(key) != value
    }
    if not changes:
        return
    db.add(ListingChangeEvent(
        listing_id=listing.id, snapshot_id=snapshot_id, actor='Импорт таблицы',
        kind='created' if previous is None else 'updated', changes=changes,
    ))
    for field, source in [('source_auto_ru', EngineType.AUTO_RU), ('source_avito', EngineType.AVITO)]:
        if field not in changes:
            continue
        old_url, new_url = changes[field]['old'], changes[field]['new']
        if new_url:
            db.add(ListingLinkEvent(
                listing_id=listing.id, source=source, old_url=old_url, new_url=new_url,
                actor='Импорт таблицы', reason=f'Снимок источника {snapshot_id}',
            ))
        close_replaced_episodes(db, listing.id, source, old_url, new_url)
