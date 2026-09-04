import asyncio
import base64

import httpx

from app.config import settings
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
            allowed = await client.get(
                '/openapi.json',
                headers={'Authorization': _basic('operator', 'long-random-password')},
            )
            assert allowed.status_code == 200

    asyncio.run(verify())
