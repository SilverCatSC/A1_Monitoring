import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import trigger_scan
from app.models import Base, ListingObservation, ScanRun


@pytest.mark.parametrize('profile', ['unknown', 'local_vpn'])
def test_scan_endpoint_rejects_untrusted_profile_before_writes(tmp_path, monkeypatch, profile):
    monkeypatch.setattr('app.service.monitor.settings.network_profile', profile)
    engine = create_engine(f'sqlite:///{tmp_path / "scan-api.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        with pytest.raises(HTTPException) as error:
            trigger_scan(db=session)

        assert error.value.status_code == 422
        assert f'current={profile}' in error.value.detail
        assert session.query(ScanRun).count() == 0
        assert session.query(ListingObservation).count() == 0
    finally:
        session.close()
        engine.dispose()
