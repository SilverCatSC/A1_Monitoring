from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.importer.service import SourceImporter, SourceImportError
from app.importer.sheet_csv import CsvOrXlsxReader
from app.models import (
    AbsenceEpisode,
    DealerListingCandidate,
    EngineType,
    FeedbackStatus,
    Listing,
    ListingObservation,
    ManagerFeedback,
    ObservationState,
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
from app.service.dealer_discovery import DealerDiscoveryService, DiscoveryAlreadyRunning
from app.service.evidence import EvidenceAccessError, resolve_observation_evidence
from app.service.feedback import (
    ALLOWED_CATEGORIES,
    ALLOWED_SEVERITIES,
    FeedbackService,
    FeedbackValidationError,
)
from app.service.filters import FilterRegistryService, FilterValidationError
from app.service.listings import ListingRegistryService, ListingValidationError
from app.service.monitor import MonitorService, ScanAlreadyRunning, ScanConfigurationError
from app.service.report import (
    dashboard_context,
    feedback_queue_context,
    kpi_overview,
    latest_scan_runs_status,
    listing_catalog_context,
    listing_detail_context,
    observation_history_context,
    operational_status,
    weekend_summary,
)

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
        'scheduler': 'enabled' if settings.scheduler_enabled else 'disabled',
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


@router.get('/status/scans/latest')
def latest_scan_status(db: Session = Depends(get_db)):
    return latest_scan_runs_status(db)


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


@router.get('/dashboard/feedback', response_class=HTMLResponse)
def feedback_queue_html(
    request: Request,
    status: str = 'open',
    severity: str | None = None,
    category: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    allowed_statuses = {'open', 'all', *(item.value for item in FeedbackStatus)}
    if status not in allowed_statuses:
        raise HTTPException(status_code=422, detail='unsupported feedback status')
    if severity and severity not in ALLOWED_SEVERITIES:
        raise HTTPException(status_code=422, detail='unsupported feedback severity')
    if category and category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail='unsupported feedback category')
    return templates.TemplateResponse(
        request=request,
        name='feedback.html',
        context={
            'context': feedback_queue_context(
                db,
                status=status,
                severity=severity,
                category=category,
                page=page,
            ),
            'auth_enabled': settings.auth_enabled,
        },
    )


@router.get('/dashboard/history', response_class=HTMLResponse)
def observation_history_html(
    request: Request,
    days: int = 30,
    source: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    state: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    try:
        parsed_source = EngineType(source) if source else None
        parsed_state = ObservationState(state) if state else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='unsupported history filter') from exc
    return templates.TemplateResponse(
        request=request,
        name='history.html',
        context={
            'context': observation_history_context(
                db,
                days=days,
                source=parsed_source,
                brand=brand,
                model=model,
                state=parsed_state,
                page=page,
            ),
            'auth_enabled': settings.auth_enabled,
        },
    )


@router.get('/dashboard/listings', response_class=HTMLResponse)
def listing_catalog_html(
    request: Request,
    q: str | None = None,
    brand: str | None = None,
    platform: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    try:
        parsed_platform = EngineType(platform) if platform else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='unsupported platform filter') from exc
    return templates.TemplateResponse(
        request=request,
        name='listing_catalog.html',
        context={
            'context': listing_catalog_context(
                db,
                query_text=q,
                brand=brand,
                platform=parsed_platform,
                page=page,
            ),
            'auth_enabled': settings.auth_enabled,
        },
    )


@router.get('/dashboard/listings/{listing_id}', response_class=HTMLResponse)
def listing_detail_html(request: Request, listing_id: str, db: Session = Depends(get_db)):
    context = listing_detail_context(db, listing_id)
    if context is None:
        raise HTTPException(status_code=404, detail='listing not found')
    return templates.TemplateResponse(
        request=request,
        name='listing_detail.html',
        context={'context': context, 'auth_enabled': settings.auth_enabled},
    )


@router.get('/observations/{observation_id}/evidence/{page_number}')
def observation_evidence(
    observation_id: str,
    page_number: int,
    db: Session = Depends(get_db),
):
    observation = db.get(ListingObservation, observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail='observation not found')
    try:
        path = resolve_observation_evidence(observation, page_number, settings.evidence_dir)
    except EvidenceAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type='image/png',
        filename=path.name,
        content_disposition_type='inline',
    )


@router.post('/scan', response_model=TriggerScanResponse)
def trigger_scan(db: Session = Depends(get_db)):
    service = MonitorService(db)
    started_at = datetime.datetime.now(datetime.UTC)
    try:
        summary = service.run_full_cycle()
    except ScanConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    except ScanConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SourceImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TriggerCycleResponse(status='ok', started_at=started_at, summary=summary)


@router.post('/dealer/discover')
def discover_dealer_listings(db: Session = Depends(get_db)):
    try:
        return DealerDiscoveryService(db).run()
    except DiscoveryAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/dealer/candidates')
def dealer_candidates(source: str | None = None, active: bool | None = True, db: Session = Depends(get_db)):
    query = db.query(DealerListingCandidate)
    if source is not None:
        if source not in {'auto_ru', 'avito'}:
            raise HTTPException(status_code=422, detail='source must be auto_ru or avito')
        query = query.filter(DealerListingCandidate.source == source)
    if active is not None:
        query = query.filter(DealerListingCandidate.active == active)
    rows = query.order_by(DealerListingCandidate.last_seen_at.desc()).limit(1000).all()
    return {
        'candidates': [
            {
                'id': row.id,
                'source': row.source.value,
                'title': row.title,
                'url': row.listing_url,
                'price_hint': row.price_hint,
                'active': row.active,
                'first_seen_at': row.first_seen_at,
                'last_seen_at': row.last_seen_at,
                'dealer_url': row.dealer_url,
                'network_profile': row.network_profile,
            }
            for row in rows
        ]
    }


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
        registry = FilterRegistryService(db)
        registry.refresh_managed_assignments()
        registry.sync_canonical_catalog()
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


@router.get('/filters/catalog/status')
def canonical_filter_catalog_status(db: Session = Depends(get_db)):
    return FilterRegistryService(db).canonical_catalog_status()


@router.post('/filters/catalog/sync')
def sync_canonical_filter_catalog(db: Session = Depends(get_db)):
    return FilterRegistryService(db).sync_canonical_catalog()


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
def change_filter_state(filter_id: str, payload: FilterStateChange, db: Session = Depends(get_db)):
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
def update_listing_link(listing_id: str, payload: ListingLinkUpdate, db: Session = Depends(get_db)):
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
            observed_id=payload.observed_id,
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
                'observed_id': item.observed_id,
                'run_id': item.run_id,
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
def update_feedback(feedback_id: str, payload: FeedbackUpdate, db: Session = Depends(get_db)):
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
