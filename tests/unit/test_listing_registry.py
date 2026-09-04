import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, EngineType, Listing, ListingLinkEvent
from app.service.listings import ListingRegistryService, ListingValidationError


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "listings.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def test_listing_link_update_is_validated_and_audited(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = Listing(
            vehicle_signature='W1VVNLTZ5S4556796',
            vin='W1VVNLTZ5S4556796',
            is_active=True,
        )
        session.add(listing)
        session.commit()

        updated = ListingRegistryService(session).update_link(
            listing.id,
            source=EngineType.AUTO_RU,
            url='https://auto.ru/cars/used/sale/mercedes/v_klasse/1132311022-car/',
            actor='Анна',
            reason='Сверено по карточке дилера',
        )

        assert updated.source_auto_ru.endswith('1132311022-car/')
        event = session.query(ListingLinkEvent).one()
        assert event.old_url is None
        assert event.new_url == updated.source_auto_ru
        assert event.actor == 'Анна'
    finally:
        session.close()
        engine.dispose()


def test_listing_link_update_rejects_search_or_wrong_domain(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = Listing(vehicle_signature='vehicle', is_active=True)
        session.add(listing)
        session.commit()
        service = ListingRegistryService(session)

        with pytest.raises(ListingValidationError, match='not a supported'):
            service.update_link(
                listing.id,
                source=EngineType.AUTO_RU,
                url='https://auto.ru/moskva/cars/all/',
                actor='Анна',
                reason='wrong type',
            )
        with pytest.raises(ListingValidationError, match='not a supported'):
            service.update_link(
                listing.id,
                source=EngineType.AUTO_RU,
                url='https://a1auto.ru/cars-for-sale/v-class.html',
                actor='Анна',
                reason='wrong host',
            )
        assert session.query(ListingLinkEvent).count() == 0
    finally:
        session.close()
        engine.dispose()
