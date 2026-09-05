from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api import router
from app.config import settings
from app.db import init_db
from app.scheduler import start_scheduler
from app.security import valid_basic_authorization, validate_security_configuration


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_security_configuration(
        environment=settings.app_env,
        enabled=settings.auth_enabled,
        username=settings.admin_username,
        password=settings.admin_password,
        network_profile=settings.network_profile,
    )
    init_db()
    scheduler = (
        start_scheduler()
        if settings.scheduler_enabled and settings.app_env in {'stage', 'production'}
        else None
    )
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title='A1 Search Monitor', lifespan=lifespan)


@app.middleware('http')
async def basic_auth(request, call_next):
    if settings.auth_enabled and request.url.path not in {
        '/api/v1/health',
        '/api/v1/ready',
    }:
        if not valid_basic_authorization(
            request.headers.get('authorization'),
            settings.admin_username,
            settings.admin_password,
        ):
            return JSONResponse(
                {'detail': 'authentication required'},
                status_code=401,
                headers={'WWW-Authenticate': 'Basic realm="A1 Search Monitor"'},
            )
    return await call_next(request)


app.include_router(router, prefix='/api/v1')
