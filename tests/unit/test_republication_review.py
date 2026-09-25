import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import confirm_reconciliation
from app.config import settings
from app.models import (
    Base,
    EngineType,
    Listing,
    ListingLinkEvent,
    ListingPlacementIdentity,
    ListingReconciliation,
    MonitoringCycle,
)
from app.schemas import ReconciliationConfirm
from app.scraper.base import EVIDENCE_SCHEMA_VERSION, evidence_manifest_name
from app.security import AuthenticatedActor
from app.service.automatic_link_sync import AutomaticLinkSyncService
from app.service.offer_reconciliation import offer_review_queue
from app.service.report import listing_catalog_context, listing_detail_context
from app.service.republication_review import exact_republication_candidate, republication_review

OLD = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1128317289-4edaae6f/'
NEW = 'https://auto.ru/cars/new/group/mercedes/v_klasse/23963640/23963648/1133841448-898cf5a5/'
OTHER = 'https://auto.ru/cars/new/group/mercedes/v_klasse/23963640/23963648/1133976818-5c335e64/'
PLACEMENT_ID = 'MBVC011220252508260001'
EVIDENCE = 'new-card.png'


def _digest(value: str | bytes) -> str:
    raw = value.encode('utf-8') if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path / 'evidence'))
    monkeypatch.setattr(settings, 'head_table_audit_dir', str(tmp_path / 'head'))
    monkeypatch.setattr(settings, 'auth_enabled', False)
    root = Path(settings.evidence_dir)
    root.mkdir()
    image = root / EVIDENCE
    image.write_bytes(b'\x89PNG\r\n\x1a\n')
    manifest = {
        'schema_version': EVIDENCE_SCHEMA_VERSION,
        'source': 'auto_ru', 'purpose': 'direct_card', 'page_number': 1,
        'screenshot_file': EVIDENCE,
        'screenshot_sha256': _digest(image.read_bytes()),
        'screenshot_bytes': image.stat().st_size,
        'captured_at': datetime.now(UTC).isoformat(),
        'requested_url_sha256': _digest(NEW), 'final_url_sha256': _digest(NEW),
    }
    (root / evidence_manifest_name(EVIDENCE)).write_text(json.dumps(manifest))
    report_dir = root / 'cycles' / 'cycle-review'
    report_dir.mkdir(parents=True)
    (report_dir / 'placement_reconciliation.json').write_text(json.dumps({
        'schema_version': 1, 'cycle_id': 'cycle-review',
        'findings': [{
            'code': 'republication_candidate', 'id_match_basis': 'exact',
            'placement_id': PLACEMENT_ID, 'operator_review_check_id': 'check-review',
            'current_url': OLD, 'observed_urls': [NEW],
        }],
        'observed_cards': [{
            'source': 'auto_ru', 'state': 'active', 'url': NEW,
            'evidence': EVIDENCE, 'evidence_manifest': evidence_manifest_name(EVIDENCE),
        }],
    }))
    engine = create_engine(f'sqlite:///{tmp_path / "republication.db"}')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(MonitoringCycle(
        id='cycle-review', status='partial', network_profile='local_browser',
        app_version='test', summary={'placement_reconciliation': {
            'report_path': 'cycles/cycle-review/placement_reconciliation.json',
        }},
    ))
    db.add(Listing(
        id='car', vehicle_signature='car', brand='Mercedes-Benz', model='V-Class',
        generation='V-VIP', source_auto_ru=OLD, is_active=True,
    ))
    db.add(ListingPlacementIdentity(
        listing_id='car', source=EngineType.AUTO_RU,
        placement_id=PLACEMENT_ID, evidence=EVIDENCE,
    ))
    db.add(ListingReconciliation(
        id='check-review', cycle_id='cycle-review', batch_id='batch-review',
        listing_id='car', source=EngineType.AUTO_RU, state='removed', url=OLD,
        reason='Старая карточка закрыта', checked_at=datetime.now(UTC),
        details={'network_profile': 'local_browser', 'direct_inspection': {
            'state': 'removed', 'status_code': 'sold',
        }},
        candidates=[
            {'id': 'model-only', 'url': OTHER, 'basis': 'Похожа модель'},
            {'id': 'id-exact', 'url': NEW, 'placement_id': PLACEMENT_ID,
             'id_match_basis': 'exact', 'evidence': EVIDENCE,
             'feed_sheet': 'autoru-feed-all', 'feed_row': 4},
        ],
    ))
    db.commit()
    return db, engine, root


def _operator_request():
    return SimpleNamespace(
        state=SimpleNamespace(actor=AuthenticatedActor(username='operator', role='operator'))
    )


def test_exact_id_republication_is_visible_and_confirmation_preserves_history(tmp_path, monkeypatch):
    db, engine, _ = _fixture(tmp_path, monkeypatch)
    try:
        record = db.get(ListingReconciliation, 'check-review')
        review = republication_review(db, record, OLD)
        assert review['confirmable'] is True
        assert review['proof_url'].endswith('/placement-evidence/0')
        catalog = listing_catalog_context(db)
        detail = listing_detail_context(db, 'car')
        environment = Environment(
            loader=FileSystemLoader(Path(__file__).parents[2] / 'src' / 'app' / 'templates'),
            autoescape=select_autoescape(['html']),
        )
        catalog_html = environment.get_template('listing_catalog.html').render(
            context=catalog, auth_enabled=False,
        )
        detail_html = environment.get_template('listing_detail.html').render(
            context=detail, auth_enabled=False, actor_name='stage', actor_role='admin',
        )
        assert 'Старая ссылка ↗' in catalog_html
        assert 'Новая карточка ↗' in catalog_html
        assert 'Проверить ID →' in catalog_html
        assert PLACEMENT_ID in detail_html
        assert OLD in detail_html and NEW in detail_html
        assert 'Снимок новой карточки ↗' in detail_html
        assert 'Подтвердить новую ссылку' in detail_html
        findings = offer_review_queue(db)['findings']
        assert [item['code'] for item in findings] == ['republication_candidate_exact_id']

        with pytest.raises(HTTPException) as error:
            confirm_reconciliation(
                record.id,
                ReconciliationConfirm(url=OTHER, actor='Оператор', reason='Похожа модель'),
                _operator_request(), db,
            )
        assert error.value.status_code == 422
        assert db.get(Listing, 'car').source_auto_ru == OLD

        confirm_reconciliation(
            record.id,
            ReconciliationConfirm(url=NEW, actor='Оператор', reason='Сверены ID и снимок'),
            _operator_request(), db,
        )
        assert db.get(Listing, 'car').source_auto_ru == NEW
        assert listing_detail_context(db, 'car')['republication_reviews']['auto_ru'] is None
        event = db.query(ListingLinkEvent).one()
        assert (event.old_url, event.new_url) == (OLD, NEW)
        assert listing_detail_context(db, 'car')['link_history'][0]['old_url'] == OLD
    finally:
        db.close()
        engine.dispose()


def test_operator_confirmation_creates_id_binding_when_missing(tmp_path, monkeypatch):
    db, engine, _ = _fixture(tmp_path, monkeypatch)
    try:
        db.query(ListingPlacementIdentity).delete()
        db.commit()
        confirm_reconciliation(
            'check-review',
            ReconciliationConfirm(url=NEW, actor='Оператор', reason='Сверены ID и снимок'),
            _operator_request(), db,
        )
        identity = db.query(ListingPlacementIdentity).one()
        assert (identity.listing_id, identity.source, identity.placement_id) == (
            'car', EngineType.AUTO_RU, PLACEMENT_ID,
        )
    finally:
        db.close()
        engine.dispose()


def test_republication_confirmation_needs_fresh_intact_evidence(tmp_path, monkeypatch):
    db, engine, root = _fixture(tmp_path, monkeypatch)
    try:
        record = db.get(ListingReconciliation, 'check-review')
        record.checked_at = datetime.now(UTC) - timedelta(days=2)
        db.commit()
        assert republication_review(db, record, OLD)['confirmable'] is False
        with pytest.raises(HTTPException) as error:
            confirm_reconciliation(
                record.id,
                ReconciliationConfirm(url=NEW, actor='Оператор', reason='Старый снимок'),
                _operator_request(), db,
            )
        assert error.value.status_code == 409

        record.checked_at = datetime.now(UTC)
        db.commit()
        (root / EVIDENCE).write_bytes(b'tampered')
        assert republication_review(db, record, OLD)['confirmable'] is False
        with pytest.raises(HTTPException) as error:
            confirm_reconciliation(
                record.id,
                ReconciliationConfirm(url=NEW, actor='Оператор', reason='Снимок повреждён'),
                _operator_request(), db,
            )
        assert error.value.status_code == 409
        assert db.get(Listing, 'car').source_auto_ru == OLD
    finally:
        db.close()
        engine.dispose()


def test_multiple_exact_id_candidates_are_not_auto_selected(tmp_path, monkeypatch):
    db, engine, _ = _fixture(tmp_path, monkeypatch)
    try:
        record = db.get(ListingReconciliation, 'check-review')
        record.candidates = [
            *record.candidates,
            {'id': 'another-exact', 'url': OTHER, 'placement_id': PLACEMENT_ID,
             'id_match_basis': 'exact', 'evidence': EVIDENCE},
        ]
        db.commit()
        assert exact_republication_candidate(record) is None
        assert listing_catalog_context(db)['cards'][0]['platforms']['auto_ru']['republication_review'] is None
    finally:
        db.close()
        engine.dispose()


def test_exact_id_link_is_synchronized_before_search_and_old_url_is_retained(tmp_path, monkeypatch):
    db, engine, _ = _fixture(tmp_path, monkeypatch)
    try:
        preflight = {'batch_id': 'batch-review', 'checks': {}}
        result = AutomaticLinkSyncService(db, cycle_id='cycle-review').run(
            preflight, {'status': 'complete', 'report_path': 'cycles/cycle-review/placement_reconciliation.json'},
        )
        assert result['status'] == 'complete'
        assert len(result['updated']) == 1
        assert db.get(Listing, 'car').source_auto_ru == NEW
        event = db.query(ListingLinkEvent).one()
        assert (event.old_url, event.new_url, event.actor) == (OLD, NEW, 'system:exact_unique_id')
        assert preflight['checks']['car:auto_ru']['url'] == NEW
        latest = db.query(ListingReconciliation).order_by(ListingReconciliation.checked_at.desc()).first()
        assert latest.url == NEW
        assert latest.details['automatic_link_sync_check_id'] == 'check-review'
    finally:
        db.close()
        engine.dispose()


def test_automatic_link_sync_refuses_broken_evidence(tmp_path, monkeypatch):
    db, engine, root = _fixture(tmp_path, monkeypatch)
    try:
        (root / EVIDENCE).write_bytes(b'tampered')
        result = AutomaticLinkSyncService(db, cycle_id='cycle-review').run(
            {'batch_id': 'batch-review', 'checks': {}},
            {'status': 'complete', 'report_path': 'cycles/cycle-review/placement_reconciliation.json'},
        )
        assert result['status'] == 'partial'
        assert result['blocked'][0]['reason'] == 'evidence_not_verified'
        assert db.get(Listing, 'car').source_auto_ru == OLD
        assert db.query(ListingLinkEvent).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_automatic_link_sync_refuses_id_without_prior_listing_binding(tmp_path, monkeypatch):
    db, engine, _ = _fixture(tmp_path, monkeypatch)
    try:
        db.query(ListingPlacementIdentity).delete()
        db.commit()
        result = AutomaticLinkSyncService(db, cycle_id='cycle-review').run(
            {'batch_id': 'batch-review', 'checks': {}},
            {'status': 'complete', 'report_path': 'cycles/cycle-review/placement_reconciliation.json'},
        )
        assert result['status'] == 'partial'
        assert result['blocked'][0]['reason'] == 'unique_id_not_bound_to_listing'
        assert db.get(Listing, 'car').source_auto_ru == OLD
    finally:
        db.close()
        engine.dispose()


def test_visual_alias_republication_does_not_auto_update_or_allow_search(tmp_path, monkeypatch):
    db, engine, root = _fixture(tmp_path, monkeypatch)
    try:
        record = db.get(ListingReconciliation, 'check-review')
        candidates = list(record.candidates)
        candidates[1] = {**candidates[1], 'id_match_basis': 'visual_alias'}
        record.candidates = candidates
        db.commit()
        report_path = root / 'cycles' / 'cycle-review' / 'placement_reconciliation.json'
        report = json.loads(report_path.read_text())
        report['findings'][0]['id_match_basis'] = 'visual_alias'
        report_path.write_text(json.dumps(report))

        result = AutomaticLinkSyncService(db, cycle_id='cycle-review').run(
            {'batch_id': 'batch-review', 'checks': {}},
            {'status': 'complete', 'report_path': 'cycles/cycle-review/placement_reconciliation.json'},
        )
        assert result['status'] == 'partial'
        assert result['blocked'][0]['reason'] == 'non_exact_or_unresolved_republication'
        assert db.get(Listing, 'car').source_auto_ru == OLD
    finally:
        db.close()
        engine.dispose()
