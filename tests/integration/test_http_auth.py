import asyncio
import base64

import httpx

from app.config import settings
from app.db import get_db
from app.main import app


def _basic(username: str, password: str) -> str:
    encoded = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return f'Basic {encoded}'


def test_http_middleware_keeps_health_public_and_protects_application(monkeypatch):
    monkeypatch.setattr(settings, 'auth_enabled', True)
    monkeypatch.setattr(settings, 'admin_username', 'operator')
    monkeypatch.setattr(settings, 'admin_password', 'long-random-password')

    async def verify():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            assert (await client.get('/api/v1/health')).status_code == 200
            denied = await client.get('/openapi.json')
            assert denied.status_code == 401
            assert denied.headers['www-authenticate'].startswith('Basic')
            for method, path in (
                ('post', '/api/v1/scan'),
                ('post', '/api/v1/cycle'),
                ('post', '/api/v1/dealer/discover'),
                ('post', '/api/v1/import'),
                ('post', '/api/v1/filters/catalog/sync'),
                ('post', '/api/v1/filters'),
                ('patch', '/api/v1/filters/test-filter'),
            ):
                response = await getattr(client, method)(path)
                assert response.status_code == 401, path
            allowed = await client.get(
                '/openapi.json',
                headers={'Authorization': _basic('operator', 'long-random-password')},
            )
            assert allowed.status_code == 200

    asyncio.run(verify())


def test_business_roles_cannot_invoke_operational_or_registry_mutations(monkeypatch):
    monkeypatch.setattr(settings, 'auth_enabled', True)
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password', 'admin-password-long')
    monkeypatch.setattr(
        settings,
        'auth_users_json',
        (
            '{"marketing":{"password":"marketing-password-long","role":"marketing"},'
            '"sales-director":{"password":"sales-director-password-long","role":"sales_director"}}'
        ),
    )

    def test_db():
        # Denied requests must never use this object to create a scan or mutate data.
        yield object()

    app.dependency_overrides[get_db] = test_db
    protected_requests = (
        ('/api/v1/scan', None),
        ('/api/v1/cycle', None),
        ('/api/v1/dealer/discover', None),
        ('/api/v1/import', None),
        ('/api/v1/filters/catalog/sync', None),
        (
            '/api/v1/filters',
            {
                'source': 'auto_ru',
                'name': 'test',
                'url': 'https://auto.ru/moskva/cars/',
                'active': True,
            },
        ),
        ('/api/v1/filters/test-filter', {'active': False}),
    )

    async def verify():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            for username, password in (
                ('marketing', 'marketing-password-long'),
                ('sales-director', 'sales-director-password-long'),
            ):
                headers = {'Authorization': _basic(username, password)}
                for path, payload in protected_requests:
                    if path == '/api/v1/filters/test-filter':
                        response = await client.patch(path, headers=headers, json=payload)
                    else:
                        response = await client.post(path, headers=headers, json=payload)
                    assert response.status_code == 403, (username, path, response.text)

    try:
        asyncio.run(verify())
    finally:
        app.dependency_overrides.pop(get_db, None)
