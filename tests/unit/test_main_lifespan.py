import asyncio

import pytest

from app.security import SecurityConfigurationError


def test_stage_lifespan_initializes_without_a_container_scheduler(monkeypatch):
    import app.main as main_module

    events = []
    monkeypatch.setattr(main_module, 'validate_security_configuration', lambda **_: None)
    monkeypatch.setattr(main_module, 'init_db', lambda: events.append('init'))
    monkeypatch.setattr(main_module.settings, 'app_env', 'stage')
    monkeypatch.setattr(main_module.settings, 'scheduler_enabled', False)

    async def run():
        async with main_module.lifespan(None):
            events.append('body')

    asyncio.run(run())
    assert events == ['init', 'body']


@pytest.mark.parametrize(
    'browser_cdp_url,host_cdp_scheduler_verified',
    [
        ('http://127.0.0.1:19222', False),
        ('http://host.docker.internal:19222', True),
    ],
)
def test_container_scheduler_is_rejected_before_database_or_background_work(
    monkeypatch, browser_cdp_url, host_cdp_scheduler_verified
):
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, 'app_env', 'stage')
    monkeypatch.setattr(main_module.settings, 'auth_enabled', False)
    monkeypatch.setattr(main_module.settings, 'admin_username', None)
    monkeypatch.setattr(main_module.settings, 'admin_password', None)
    monkeypatch.setattr(main_module.settings, 'auth_users_json', None)
    monkeypatch.setattr(main_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(main_module.settings, 'scheduler_enabled', True)
    monkeypatch.setattr(main_module.settings, 'browser_cdp_url', browser_cdp_url)
    monkeypatch.setattr(
        main_module.settings,
        'host_cdp_scheduler_verified',
        host_cdp_scheduler_verified,
    )
    monkeypatch.setattr(main_module, 'init_db', lambda: pytest.fail('database must not initialize'))

    async def run():
        async with main_module.lifespan(None):
            pytest.fail('lifespan body must not run')

    with pytest.raises(SecurityConfigurationError, match='SCHEDULER_ENABLED=true is not accepted'):
        asyncio.run(run())
