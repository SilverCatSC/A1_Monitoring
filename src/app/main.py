from __future__ import annotations

from fastapi import FastAPI

from app.api import router
from app.config import settings
from app.db import init_db
from app.scheduler import start_scheduler

app = FastAPI(title='A1 Search Monitor')


@app.on_event('startup')
def on_startup():
    init_db()
    if settings.app_env == 'production':
        start_scheduler()


app.include_router(router, prefix='/api/v1')
