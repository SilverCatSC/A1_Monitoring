from contextlib import contextmanager


def test_scheduler_coalesces_missed_intervals_and_logs_job_failure(monkeypatch):
    import app.scheduler as scheduler_module

    events = []

    class Scheduler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def add_job(self, func, *args, **kwargs):
            self.job = func
            self.job_args = args
            self.job_kwargs = kwargs

        def start(self):
            events.append('started')

    @contextmanager
    def broken_database():
        raise RuntimeError('database unavailable')
        yield None

    monkeypatch.setattr(scheduler_module, 'BackgroundScheduler', Scheduler)
    monkeypatch.setattr(scheduler_module, 'get_db_context', broken_database)
    monkeypatch.setattr(
        scheduler_module.logger,
        'exception',
        lambda message: events.append(message),
    )

    scheduler = scheduler_module.start_scheduler()
    scheduler.job()

    assert scheduler.kwargs['job_defaults'] == {
        'coalesce': True,
        'max_instances': 1,
        'misfire_grace_time': 300,
    }
    assert scheduler.job_args == ('interval',)
    assert scheduler.job_kwargs['id'] == 'import_and_monitor_cycle'
    assert events == ['started', 'scheduled monitoring cycle failed']
