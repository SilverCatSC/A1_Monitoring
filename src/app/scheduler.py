from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.db import get_db_context
from app.service.cycle import MonitoringCycleService

logger = logging.getLogger(__name__)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        timezone=settings.app_tz,
        job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 300},
    )

    def run_scan_job():
        try:
            with get_db_context() as db:
                MonitoringCycleService(db).run()
        except Exception:
            # MonitoringCycleService writes the durable failure when the cycle
            # has been registered. APScheduler logging keeps scheduler faults
            # visible instead of silently dropping the regular job.
            logger.exception('scheduled monitoring cycle failed')

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
