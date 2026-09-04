from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.db import get_db_context
from app.service.cycle import MonitoringCycleService


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.app_tz)

    def run_scan_job():
        with get_db_context() as db:
            MonitoringCycleService(db).run()

    scheduler.add_job(
        run_scan_job,
        'interval',
        minutes=settings.scan_interval_minutes,
        id='import_and_monitor_cycle',
        replace_existing=True,
        next_run_time=datetime.now(tz=timezone.utc),
    )
    scheduler.start()
    return scheduler
