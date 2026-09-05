import asyncio


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
