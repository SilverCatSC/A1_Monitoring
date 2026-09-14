from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import settings
from app.db import init_db
from app.security import (
    AuthenticatedActor,
    authenticate_basic_authorization,
    validate_security_configuration,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_security_configuration(
        environment=settings.app_env,
        enabled=settings.auth_enabled,
        username=settings.admin_username,
        password=settings.admin_password,
        network_profile=settings.network_profile,
        auth_users_json=settings.auth_users_json,
        scheduler_enabled=settings.scheduler_enabled,
        browser_cdp_url=settings.browser_cdp_url,
        host_cdp_scheduler_verified=settings.host_cdp_scheduler_verified,
    )
    init_db()
    yield


app = FastAPI(title='A1 Search Monitor', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')


@app.middleware('http')
async def basic_auth(request, call_next):
    if settings.auth_enabled and request.url.path not in {
        '/api/v1/health',
        '/api/v1/ready',
    }:
        actor = authenticate_basic_authorization(
            request.headers.get('authorization'),
            admin_username=settings.admin_username,
            admin_password=settings.admin_password,
            auth_users_json=settings.auth_users_json,
        )
        if actor is None:
            return JSONResponse(
                {'detail': 'authentication required'},
                status_code=401,
                headers={'WWW-Authenticate': 'Basic realm="A1 Search Monitor"'},
            )
        request.state.actor = actor
    elif not settings.auth_enabled:
        request.state.actor = AuthenticatedActor(username='stage', role='admin')
    return await call_next(request)


app.include_router(router, prefix='/api/v1')
