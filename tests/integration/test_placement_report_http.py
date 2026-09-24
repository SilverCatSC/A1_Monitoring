import asyncio
import base64
import json

import httpx

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import EngineType, MonitoringCycle
from app.scraper.base import _write_evidence_manifest, evidence_manifest_name


class _Query:
    def __init__(self, cycle):
        self.cycle = cycle

    def order_by(self, *_args):
        return self

    def first(self):
        return self.cycle

    def limit(self, _count):
        return self

    def all(self):
        return [self.cycle]


class _DB:
    def __init__(self, cycle):
        self.cycle = cycle

    def get(self, _model, cycle_id):
        return self.cycle if self.cycle.id == cycle_id else None

    def query(self, _model):
        return _Query(self.cycle)


def test_cycle_report_is_visible_read_only_in_api_and_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'auth_enabled', False)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    cycle_id = 'cycle-1'
    report_path = f'cycles/{cycle_id}/placement_reconciliation.json'
    target = tmp_path / report_path
    target.parent.mkdir(parents=True)
    card_url = 'https://www.avito.ru/moskva/avtomobili/example_8176281881'
    image = tmp_path / 'avito-card.png'
    image.write_bytes(b'fixture-image')
    _write_evidence_manifest(
        image, source=EngineType.AVITO, purpose='direct_card', page_number=1,
        requested_url=card_url, final_url=card_url,
    )
    target.write_text(json.dumps({
        'schema_version': 1,
        'cycle_id': cycle_id,
        'created_at_utc': '2026-09-24T12:00:00+00:00',
        'catalogue_complete': {'auto_ru': True, 'avito': True},
        'new_card_checks': 1,
        'observed_cards': [{
            'source': 'avito', 'url': card_url, 'state': 'active',
            'evidence': image.name, 'evidence_manifest': evidence_manifest_name(image.name),
        }],
        'findings': [{
            'code': 'republication_candidate', 'placement_id': 'MBVC011220262508260009',
            'reason': 'same ID on a new URL', 'feed_sheet': 'avito-feed-new',
            'feed_row': 9, 'id_match_basis': 'visual_alias',
            'observed_urls': [card_url],
            'current_url': 'https://www.avito.ru/moskva/avtomobili/example_8047929828',
        }],
    }))
    cycle = MonitoringCycle(
        id=cycle_id, status='completed',
        summary={'placement_reconciliation': {'status': 'complete', 'report_path': report_path}},
    )

    def db():
        yield _DB(cycle)

    app.dependency_overrides[get_db] = db
    try:
        async def verify():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                report = await client.get(f'/api/v1/status/cycles/{cycle_id}/placement-report')
                assert report.status_code == 200
                assert report.json()['findings'][0]['code'] == 'republication_candidate'
                page = await client.get('/api/v1/dashboard/placement-identity')
                assert page.status_code == 200
                assert 'republication_candidate' in page.text
                assert 'Возможная перевыкладка' in page.text
                assert 'MBVC011220262508260009' in page.text
                assert 'Снимок карточки' in page.text
                evidence = await client.get(
                    f'/api/v1/status/cycles/{cycle_id}/placement-evidence/0'
                )
                assert evidence.status_code == 200
                assert evidence.content == b'fixture-image'
                payload = json.loads(target.read_text())
                payload['observed_cards'][0]['evidence_manifest'] = 'wrong.json'
                target.write_text(json.dumps(payload))
                assert (await client.get(
                    f'/api/v1/status/cycles/{cycle_id}/placement-evidence/0'
                )).status_code == 404
                payload['observed_cards'][0]['evidence_manifest'] = evidence_manifest_name(image.name)
                target.write_text(json.dumps(payload))
                image.write_bytes(b'tampered')
                assert (await client.get(
                    f'/api/v1/status/cycles/{cycle_id}/placement-evidence/0'
                )).status_code == 404
                monkeypatch.setattr(settings, 'auth_enabled', True)
                monkeypatch.setattr(settings, 'admin_username', 'local-admin')
                monkeypatch.setattr(settings, 'admin_password', 'local-long-password')
                assert (await client.get(
                    f'/api/v1/status/cycles/{cycle_id}/placement-report'
                )).status_code == 401
                token = base64.b64encode(b'local-admin:local-long-password').decode()
                headers = {'Authorization': f'Basic {token}'}
                assert (await client.get(
                    f'/api/v1/status/cycles/{cycle_id}/placement-report', headers=headers,
                )).status_code == 200
        asyncio.run(verify())
    finally:
        app.dependency_overrides.clear()
