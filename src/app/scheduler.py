from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.db import get_db_context
from app.service.monitor import MonitorService


def start_scheduler() -> None:
    scheduler = BackgroundScheduler(timezone=settings.app_tz)

    def run_scan_job():
        with get_db_context() as db:
            MonitorService(db).run_full_cycle()

    scheduler.add_job(
        run_scan_job,
        'interval',
        minutes=settings.scan_interval_minutes,
        id='monitor_cycle',
        replace_existing=True,
        next_run_time=datetime.now(tz=timezone.utc),
    )
    scheduler.start()
