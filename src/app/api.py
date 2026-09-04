from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.importer.service import SourceImporter, SourceImportError
from app.importer.sheet_csv import CsvOrXlsxReader
from app.models import (
    AbsenceEpisode,
    FeedbackStatus,
    Listing,
    ManagerFeedback,
    SearchFilter,
    SourceImportSnapshot,
)
from app.schemas import (
    FeedbackCreate,
    FeedbackUpdate,
    FilterStateChange,
    FilterUpsert,
    HealthResponse,
    ImportResponse,
    KPIResponse,
    ListingLinkUpdate,
    TriggerCycleResponse,
    TriggerScanResponse,
)
from app.service.cycle import MonitoringCycleService
from app.service.feedback import FeedbackService, FeedbackValidationError
from app.service.filters import FilterRegistryService, FilterValidationError
from app.service.listings import ListingRegistryService, ListingValidationError
from app.service.monitor import MonitorService, ScanAlreadyRunning
from app.service.report import dashboard_context, kpi_overview, operational_status, weekend_summary

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')


@router.get('/health', response_model=HealthResponse)
def health():
    return HealthResponse(
        status='ok',
        timestamp=datetime.datetime.now(datetime.UTC),
        app_version=settings.app_version,
    )


@router.get('/ready')
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
    except Exception as exc:
        raise HTTPException(status_code=503, detail='database unavailable') from exc
    return {
        'status': 'ready',
        'database': 'ok',
        'environment': settings.app_env,
        'authentication': 'enabled' if settings.auth_enabled else 'disabled',
    }


@router.get('/dashboard/kpi')
def dashboard_kpi(days: int = 7, db: Session = Depends(get_db)):
    payload = kpi_overview(db, days=days)
    return KPIResponse(**payload)


@router.get('/dashboard/missing')
def dashboard_missing(db: Session = Depends(get_db)):
    rows = (
        db.query(AbsenceEpisode)
        .filter(AbsenceEpisode.open.is_(True))
        .order_by(AbsenceEpisode.started_at.desc())
        .all()
    )
    result = [
        {
            'listing_id': r.listing_id,
            'filter_id': r.filter_id,
            'source': r.source.value,
            'consecutive_misses': r.consecutive_misses,
            'started_at': r.started_at,
            'open': r.open,
        }
        for r in rows
    ]
    return {'rows': result}


@router.get('/dashboard/weekends')
def dashboard_weekends(days: int = 14, db: Session = Depends(get_db)):
    safe_days = min(max(days, 1), 90)
    return {'days': safe_days, 'rows': weekend_summary(db, days=safe_days)}


@router.get('/system/status')
def system_status(db: Session = Depends(get_db)):
    return operational_status(db)


@router.get('/dashboard', response_class=HTMLResponse)
def dashboard_html(request: Request, days: int = 7, db: Session = Depends(get_db)):
    safe_days = min(max(days, 1), 90)
    return templates.TemplateResponse(
        request=request,
        name='dashboard.html',
        context={
            'context': dashboard_context(db, days=safe_days),
            'auth_enabled': settings.auth_enabled,
        },
    )


@router.post('/scan', response_model=TriggerScanResponse)
def trigger_scan(db: Session = Depends(get_db)):
    service = MonitorService(db)
    started_at = datetime.datetime.now(datetime.UTC)
    try:
        summary = service.run_full_cycle()
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TriggerScanResponse(status='ok', started_at=started_at, summary=summary)


@router.post('/cycle', response_model=TriggerCycleResponse)
def trigger_cycle(db: Session = Depends(get_db)):
    started_at = datetime.datetime.now(datetime.UTC)
    try:
        summary = MonitoringCycleService(db).run()
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SourceImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TriggerCycleResponse(status='ok', started_at=started_at, summary=summary)


@router.post('/import', response_model=ImportResponse)
def import_source(file_path: str | None = None, db: Session = Depends(get_db)):
    source_path = file_path or settings.source_google_sheet_export_url or settings.source_csv_path
    if not source_path:
        raise HTTPException(status_code=400, detail='file_path required')
    reader = CsvOrXlsxReader(source_path)
    rows = reader.read()
    importer = SourceImporter(db)
    try:
        summary = importer.run(rows, source_signature=source_path)
        FilterRegistryService(db).refresh_managed_assignments()
    except SourceImportError as exc:
        # keep last-good snapshot and still expose drift diagnostics
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ImportResponse(**summary)


@router.get('/filters')
def list_filters(db: Session = Depends(get_db)):
    filters = db.query(SearchFilter).order_by(SearchFilter.name.asc()).all()
    return {
        'filters': [
            {
                'id': f.id,
                'name': f.name,
                'source': f.source.value,
                'url': f.raw_url,
                'active': f.active,
                'expectations': len(f.expectations),
            }
            for f in filters
        ]
    }


@router.post('/filters')
def upsert_filter(payload: FilterUpsert, db: Session = Depends(get_db)):
    try:
        return FilterRegistryService(db).upsert(
            source=payload.source,
            name=payload.name,
            url=payload.url,
            active=payload.active,
            vins=payload.vins,
            apply_to_all_active=payload.apply_to_all_active,
        )
    except FilterValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch('/filters/{filter_id}')
def change_filter_state(
    filter_id: str, payload: FilterStateChange, db: Session = Depends(get_db)
):
    try:
        entity = FilterRegistryService(db).set_active(filter_id, payload.active)
    except FilterValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {'id': entity.id, 'active': entity.active}


@router.get('/listings')
def list_listings(
    active: bool | None = None,
    missing_source: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    query = db.query(Listing)
    if active is not None:
        query = query.filter(Listing.is_active == active)
    if missing_source == 'auto_ru':
        query = query.filter(Listing.source_auto_ru.is_(None))
    elif missing_source == 'avito':
        query = query.filter(Listing.source_avito.is_(None))
    elif missing_source is not None:
        raise HTTPException(status_code=422, detail='missing_source must be auto_ru or avito')
    rows = query.order_by(Listing.brand, Listing.model, Listing.vin).limit(min(max(limit, 1), 1000)).all()
    return {
        'listings': [
            {
                'id': item.id,
                'vin': item.vin,
                'brand': item.brand,
                'model': item.model,
                'year': item.year,
                'active': item.is_active,
                'auto_ru_url': item.source_auto_ru,
                'avito_url': item.source_avito,
            }
            for item in rows
        ]
    }


@router.patch('/listings/{listing_id}/links')
def update_listing_link(
    listing_id: str, payload: ListingLinkUpdate, db: Session = Depends(get_db)
):
    try:
        listing = ListingRegistryService(db).update_link(
            listing_id,
            source=payload.source,
            url=payload.url,
            actor=payload.actor,
            reason=payload.reason,
        )
    except ListingValidationError as exc:
        code = 404 if str(exc) == 'listing not found' else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {
        'id': listing.id,
        'auto_ru_url': listing.source_auto_ru,
        'avito_url': listing.source_avito,
    }


@router.post('/feedback')
def create_feedback(payload: FeedbackCreate, db: Session = Depends(get_db)):
    service = FeedbackService(db)
    try:
        feedback_id = service.create(
            message=payload.message,
            listing_id=payload.listing_id,
            filter_id=payload.filter_id,
            severity=payload.severity,
            category=payload.category,
            manager_name=payload.manager_name,
            source=payload.source,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'feedback_id': feedback_id, 'status': 'created'}


@router.get('/feedback')
def list_feedback(status: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ManagerFeedback)
    if status:
        try:
            parsed_status = FeedbackStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail='unsupported feedback status') from exc
        query = query.filter(ManagerFeedback.status == parsed_status)
    rows = query.order_by(ManagerFeedback.created_at.desc()).limit(500).all()
    return {
        'feedback': [
            {
                'id': item.id,
                'listing_id': item.listing_id,
                'filter_id': item.filter_id,
                'category': item.category,
                'severity': item.severity,
                'message': item.message,
                'manager_name': item.manager_name,
                'assignee': item.assignee,
                'status': item.status.value,
                'created_at': item.created_at,
                'closed_at': item.closed_at,
                'events': [
                    {
                        'from': event.from_status,
                        'to': event.to_status,
                        'actor': event.actor,
                        'note': event.note,
                        'created_at': event.created_at,
                    }
                    for event in item.events
                ],
            }
            for item in rows
        ]
    }


@router.patch('/feedback/{feedback_id}')
def update_feedback(
    feedback_id: str, payload: FeedbackUpdate, db: Session = Depends(get_db)
):
    try:
        item = FeedbackService(db).update_status(
            feedback_id,
            payload.status,
            actor=payload.actor,
            note=payload.note,
            assignee=payload.assignee,
        )
    except FeedbackValidationError as exc:
        code = 404 if str(exc) == 'feedback not found' else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {'id': item.id, 'status': item.status.value, 'assignee': item.assignee}


@router.get('/status/imports')
def import_status(db: Session = Depends(get_db)):
    last = db.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
    if not last:
        return Response(status_code=204)
    return {
        'snapshot_id': last.id,
        'finished': last.finished_at is not None,
        'rows_total': last.raw_rows,
        'rows_valid': last.valid_rows,
        'rows_invalid': last.invalid_rows,
        'blocked_by_schema_drift': last.blocked_by_schema_drift,
    }
