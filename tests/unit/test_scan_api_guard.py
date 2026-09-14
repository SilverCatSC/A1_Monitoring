from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import trigger_scan
from app.models import Base, ListingObservation, ScanRun
from app.security import AuthenticatedActor


def _operator_request():
    return SimpleNamespace(
        state=SimpleNamespace(actor=AuthenticatedActor(username='operator', role='operator'))
    )


@pytest.mark.parametrize('profile', ['unknown', 'local_vpn'])
def test_scan_endpoint_rejects_untrusted_profile_before_writes(tmp_path, monkeypatch, profile):
    monkeypatch.setattr('app.service.monitor.settings.network_profile', profile)
    engine = create_engine(f'sqlite:///{tmp_path / "scan-api.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        with pytest.raises(HTTPException) as error:
            trigger_scan(request=_operator_request(), db=session)

        assert error.value.status_code == 422
        assert f'current={profile}' in error.value.detail
        assert session.query(ScanRun).count() == 0
        assert session.query(ListingObservation).count() == 0
    finally:
        session.close()
        engine.dispose()
