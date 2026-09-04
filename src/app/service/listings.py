from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import EngineType, Listing, ListingLinkEvent
from app.scraper.base import is_marketplace_listing_url
from app.service.filters import FilterRegistryService


class ListingValidationError(ValueError):
    pass


class ListingRegistryService:
    def __init__(self, db: Session):
        self.db = db

    def update_link(
        self,
        listing_id: str,
        *,
        source: EngineType,
        url: str,
        actor: str,
        reason: str,
    ) -> Listing:
        listing = self.db.query(Listing).filter(Listing.id == listing_id).one_or_none()
        if listing is None:
            raise ListingValidationError('listing not found')
        clean_actor = actor.strip()
        clean_reason = reason.strip()
        clean_url = url.strip()
        if not clean_actor or not clean_reason:
            raise ListingValidationError('actor and reason are required')
        if not is_marketplace_listing_url(source, clean_url):
            raise ListingValidationError(f'URL is not a supported {source.value} listing page')

        field = 'source_auto_ru' if source == EngineType.AUTO_RU else 'source_avito'
        old_url = getattr(listing, field)
        setattr(listing, field, clean_url)
        self.db.add(
            ListingLinkEvent(
                listing_id=listing.id,
                source=source,
                old_url=old_url,
                new_url=clean_url,
                actor=clean_actor,
                reason=clean_reason,
            )
        )
        self.db.flush()
        FilterRegistryService(self.db).refresh_managed_assignments()
        return listing
