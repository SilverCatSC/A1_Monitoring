import asyncio

import pytest

from app.security import SecurityConfigurationError


def test_stage_scheduler_requires_explicit_enable(monkeypatch):
    import app.main as main_module

    events = []
    monkeypatch.setattr(main_module, 'validate_security_configuration', lambda **_: None)
    monkeypatch.setattr(main_module, 'init_db', lambda: events.append('init'))
    monkeypatch.setattr(
        main_module,
        'start_scheduler',
        lambda: (_ for _ in ()).throw(AssertionError('scheduler must stay disabled')),
    )
    monkeypatch.setattr(main_module.settings, 'app_env', 'stage')
    monkeypatch.setattr(main_module.settings, 'scheduler_enabled', False)

    async def run():
        async with main_module.lifespan(None):
            events.append('body')

    asyncio.run(run())
    assert events == ['init', 'body']


def test_enabled_scheduler_is_shutdown_with_application(monkeypatch):
    import app.main as main_module

    events = []

    class Scheduler:
        def shutdown(self, wait):
            events.append(('shutdown', wait))

    monkeypatch.setattr(main_module, 'validate_security_configuration', lambda **_: None)
    monkeypatch.setattr(main_module, 'init_db', lambda: events.append('init'))
    monkeypatch.setattr(
        main_module, 'start_scheduler', lambda: events.append('start') or Scheduler()
    )
    monkeypatch.setattr(main_module.settings, 'app_env', 'production')
    monkeypatch.setattr(main_module.settings, 'scheduler_enabled', True)

    async def run():
        async with main_module.lifespan(None):
            events.append('body')

    asyncio.run(run())
    assert events == ['init', 'start', 'body', ('shutdown', False)]


def test_local_browser_scheduler_fails_before_database_or_background_work(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, 'app_env', 'stage')
    monkeypatch.setattr(main_module.settings, 'auth_enabled', False)
    monkeypatch.setattr(main_module.settings, 'admin_username', None)
    monkeypatch.setattr(main_module.settings, 'admin_password', None)
    monkeypatch.setattr(main_module.settings, 'auth_users_json', None)
    monkeypatch.setattr(main_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(main_module.settings, 'scheduler_enabled', True)
    monkeypatch.setattr(main_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(main_module.settings, 'host_cdp_scheduler_verified', False)
    monkeypatch.setattr(main_module, 'init_db', lambda: pytest.fail('database must not initialize'))
    monkeypatch.setattr(
        main_module,
        'start_scheduler',
        lambda: pytest.fail('scheduler must not start'),
    )

    async def run():
        async with main_module.lifespan(None):
            pytest.fail('lifespan body must not run')

    with pytest.raises(SecurityConfigurationError, match='HOST_CDP_SCHEDULER_VERIFIED'):
        asyncio.run(run())


def test_verified_host_cdp_allows_local_browser_scheduler(monkeypatch):
    import app.main as main_module

    events = []

    class Scheduler:
        def shutdown(self, wait):
            events.append(('shutdown', wait))

    monkeypatch.setattr(main_module.settings, 'app_env', 'stage')
    monkeypatch.setattr(main_module.settings, 'auth_enabled', False)
    monkeypatch.setattr(main_module.settings, 'admin_username', None)
    monkeypatch.setattr(main_module.settings, 'admin_password', None)
    monkeypatch.setattr(main_module.settings, 'auth_users_json', None)
    monkeypatch.setattr(main_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(main_module.settings, 'scheduler_enabled', True)
    monkeypatch.setattr(
        main_module.settings, 'browser_cdp_url', 'http://host.docker.internal:19222'
    )
    monkeypatch.setattr(main_module.settings, 'host_cdp_scheduler_verified', True)
    monkeypatch.setattr(main_module, 'init_db', lambda: events.append('init'))
    monkeypatch.setattr(main_module, 'start_scheduler', lambda: events.append('start') or Scheduler())

    async def run():
        async with main_module.lifespan(None):
            events.append('body')

    asyncio.run(run())
    assert events == ['init', 'start', 'body', ('shutdown', False)]
