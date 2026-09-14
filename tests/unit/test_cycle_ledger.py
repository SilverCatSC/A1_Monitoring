import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, EngineType, Listing, MonitoringCycle, SearchFilter, VehicleFilterExpectation
from app.service.cycle_ledger import CycleLedgerError, CycleLedgerService


def test_cycle_ledger_seals_an_immutable_privacy_bounded_roster(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "cycle.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr('app.service.cycle_ledger.settings.network_profile', 'local_browser')
    monkeypatch.setattr('app.service.cycle_ledger.settings.app_version', '0.10.0')
    try:
        listing = Listing(
            id='listing-1',
            vehicle_signature='W1VVNLTZ5S4556796',
            vin='W1VVNLTZ5S4556796',
            source_auto_ru='https://auto.ru/cars/new/group/mercedes/vle/1133334954-b51707f0/',
        )
        search_filter = SearchFilter(
            id='filter-1',
            source=EngineType.AUTO_RU,
            external_key='vle',
            name='VLE',
            raw_url='https://auto.ru/moskva/cars/mercedes/vle/new/',
        )
        session.add_all([listing, search_filter])
        session.flush()
        session.add(VehicleFilterExpectation(listing_id=listing.id, filter_id=search_filter.id))
        session.commit()

        ledger = CycleLedgerService(session, evidence_dir=str(tmp_path / 'evidence'))
        cycle = ledger.start()
        sealed = ledger.seal_roster(cycle.id, 'snapshot-1')

        path = tmp_path / 'evidence' / sealed['manifest_path']
        manifest = json.loads(path.read_text(encoding='utf-8'))
        assert manifest['cycle_id'] == cycle.id
        assert manifest['roster_sha256'] == sealed['roster_sha256']
        assert manifest['roster_count'] == 1
        assert manifest['roster'][0]['sources']['auto_ru'] == 'auto_ru:1133334954'
        assert 'W1VVNLTZ5S4556796' not in path.read_text(encoding='utf-8')
        assert session.get(MonitoringCycle, cycle.id).status == 'running'
        with pytest.raises(CycleLedgerError, match='already exists'):
            ledger.seal_roster(cycle.id, 'snapshot-1')
    finally:
        session.close()
        engine.dispose()


def test_retry_creates_a_new_cycle_with_parent_provenance(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "retry.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr('app.service.cycle_ledger.settings.network_profile', 'local_browser')
    try:
        ledger = CycleLedgerService(session, evidence_dir=str(tmp_path / 'evidence'))
        parent = ledger.start()
        parent.status = 'partial'
        parent.finished_at = datetime.now(UTC)
        session.commit()

        retry = ledger.start(retry_of_cycle_id=parent.id)

        assert retry.id != parent.id
        assert retry.retry_of_cycle_id == parent.id
        assert retry.status == 'preparing'
        parent.status = 'completed'
        session.commit()
        with pytest.raises(CycleLedgerError, match='only a finished partial or failed'):
            ledger.start(retry_of_cycle_id=parent.id)
    finally:
        session.close()
        engine.dispose()


def test_recover_open_cycles_preserves_evidence_and_terminal_history(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "recovery.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr('app.service.cycle_ledger.settings.network_profile', 'local_browser')
    try:
        ledger = CycleLedgerService(session, evidence_dir=str(tmp_path / 'evidence'))
        open_cycle = ledger.start()
        open_cycle.status = 'running'
        open_cycle.manifest_path = 'cycles/open/manifest.json'
        terminal = ledger.start()
        terminal.status = 'partial'
        terminal.finished_at = datetime.now(UTC)
        session.commit()

        recovered = ledger.recover_open_cycles(actor='operator@example.test')

        assert [row['id'] for row in recovered] == [open_cycle.id]
        refreshed = session.get(MonitoringCycle, open_cycle.id)
        assert refreshed.status == 'failed'
        assert refreshed.finished_at is not None
        assert refreshed.manifest_path == 'cycles/open/manifest.json'
        assert refreshed.summary['reason'] == 'interrupted_runner_recovery'
        assert refreshed.summary['recovered_by'] == 'operator@example.test'
        assert session.get(MonitoringCycle, terminal.id).status == 'partial'
        assert ledger.ensure_retryable(open_cycle.id).id == open_cycle.id
        assert ledger.recover_open_cycles(actor='operator@example.test') == []
    finally:
        session.close()
        engine.dispose()
