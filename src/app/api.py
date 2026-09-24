from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.access import (
    ROLE_LABELS,
    can_create_feedback,
    can_transition_feedback,
    current_actor,
    require_roles,
)
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
    ListingReconciliation,
    ManagerFeedback,
    MonitoringCycle,
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
    OfferFindingFeedbackCreate,
    ReconciliationConfirm,
    TriggerCycleResponse,
    TriggerScanResponse,
)
from app.scraper.base import canonical_listing_key, evidence_manifest_name, is_marketplace_listing_url
from app.service.analytics import STATES, activity_context, analytics_context
from app.service.company_site_report import company_site_audit_context
from app.service.cycle import (
    CycleConfigurationError,
    MonitoringCycleService,
    cycle_lock,
    require_local_browser_vpn_admission,
    require_macos_primary_monitoring_profile,
)
from app.service.cycle_ledger import CycleLedgerError
from app.service.dealer_discovery import DealerDiscoveryService, DiscoveryAlreadyRunning
from app.service.evidence import (
    EvidenceAccessError,
    read_evidence_manifest,
    resolve_named_evidence,
    resolve_observation_evidence,
)
from app.service.exception_report import offer_exception_report
from app.service.feedback import (
    ALLOWED_CATEGORIES,
    ALLOWED_SEVERITIES,
    FeedbackService,
    FeedbackValidationError,
)
from app.service.filters import FilterRegistryService, FilterValidationError
from app.service.listings import ListingRegistryService, ListingValidationError
from app.service.monitor import ScanAlreadyRunning, ScanConfigurationError
from app.service.offer_reconciliation import offer_review_queue
from app.service.placement_report import PlacementReportError, read_cycle_placement_report
from app.service.public_report import public_report_context
from app.service.reconciliation import reconciliation_context
from app.service.report import (
    cycle_operational_metrics,
    dashboard_context,
    feedback_queue_context,
    kpi_overview,
    latest_scan_runs_status,
    listing_catalog_context,
    listing_detail_context,
    observation_history_context,
    operational_status,
    recent_monitoring_cycles,
    weekend_summary,
)
from app.service.scan_progress import read_scan_progress

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')
PLACEMENT_REVIEW_LABELS = {
    'republication_candidate': ('Возможная перевыкладка', 'Сверьте снимок новой карточки и подтвердите ссылку у оператора.'),
    'mixed_script_id': ('Похожая кириллическая буква в ID', 'Машина сопоставлена с предупреждением; исправление текста не требуется для проверки.'),
    'mixed_script_feed_id': ('Ошибка буквы в фиде', 'Проверка продолжается, но исходный ID фида требует исправления.'),
    'platform_id_candidate': ('Совпал только номер Avito', 'Не меняйте ссылку без ID из описания или ручного подтверждения.'),
    'identity_conflict': ('Противоречие идентификаторов', 'Проверьте фид и карточку; связь автоматически не установлена.'),
    'duplicate_feed_id': ('Повтор ID в фиде', 'Устраните неоднозначность перед подтверждением ссылки.'),
    'duplicate_platform_id': ('Повтор AvitoId в фиде', 'Проверьте обе строки фида; номер площадки не даёт однозначной связи.'),
    'duplicate_public_id': ('Несколько карточек с одним ID', 'Определите актуальную публикацию вручную.'),
    'hidden_but_public': ('Скрыто в фиде, видно на площадке', 'Сверьте статус публикации, не делая вывода об оплате.'),
    'invalid_card_id': ('Некорректный ID карточки', 'Проверьте исходный текст объявления.'),
    'invalid_feed_id': ('Некорректный ID фида', 'Проверьте исходную строку фида.'),
    'card_evidence_missing': ('Нет подтверждённого снимка', 'Повторите контролируемое открытие карточки.'),
    'current_link_missing': ('Нет ссылки в реестре', 'Проверьте строку реестра и найденную карточку.'),
    'current_link_invalid': ('Некорректная ссылка в реестре', 'Проверьте ссылку и найденную карточку.'),
    'vehicle_anchor_missing': ('Нет связи с автомобилем', 'Нужен VIN или другой подтверждённый внутренний идентификатор.'),
    'public_id_without_feed_row': ('Карточка без строки фида', 'Проверьте публикацию и актуальность выгрузки.'),
}


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


@router.get('/status/operations')
def operations_status(db: Session = Depends(get_db)):
    return cycle_operational_metrics(db)


@router.get('/status/scans/latest')
def latest_scan_status(cycle_id: str | None = None, db: Session = Depends(get_db)):
    if cycle_id is not None and db.get(MonitoringCycle, cycle_id) is None:
        raise HTTPException(status_code=404, detail='monitoring cycle not found')
    return latest_scan_runs_status(db, cycle_id=cycle_id)


@router.get('/status/cycles/{cycle_id}')
def monitoring_cycle_status(cycle_id: str, db: Session = Depends(get_db)):
    cycle = db.get(MonitoringCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail='monitoring cycle not found')
    return {
        'id': cycle.id,
        'status': cycle.status,
        'started_at': cycle.started_at,
        'finished_at': cycle.finished_at,
        'network_profile': cycle.network_profile,
        'app_version': cycle.app_version,
        'roster_count': cycle.roster_count,
        'roster_sha256': cycle.roster_sha256,
        'manifest_path': cycle.manifest_path,
        'summary': cycle.summary,
        'error': cycle.error,
        'retry_of_cycle_id': cycle.retry_of_cycle_id,
    }


@router.get('/status/cycles/{cycle_id}/placement-report')
def monitoring_cycle_placement_report(
    cycle_id: str, request: Request, db: Session = Depends(get_db),
):
    require_roles(request, 'admin', 'operator', 'marketing', 'sales_director')
    cycle = db.get(MonitoringCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail='monitoring cycle not found')
    try:
        return read_cycle_placement_report(cycle, settings.evidence_dir)
    except PlacementReportError as exc:
        code = 404 if str(exc) == 'report_not_available' else 409
        raise HTTPException(status_code=code, detail='placement report unavailable') from exc


@router.get('/status/cycles/{cycle_id}/placement-evidence/{index}')
def monitoring_cycle_placement_evidence(
    cycle_id: str, index: int, request: Request, db: Session = Depends(get_db),
):
    require_roles(request, 'admin', 'operator', 'marketing', 'sales_director')
    cycle = db.get(MonitoringCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail='monitoring cycle not found')
    try:
        report = read_cycle_placement_report(cycle, settings.evidence_dir)
        cards = report['observed_cards']
        if index < 0 or index >= len(cards) or not isinstance(cards[index], dict):
            raise EvidenceAccessError('card evidence not found')
        stored = cards[index].get('evidence')
        if not isinstance(stored, str) or cards[index].get('evidence_manifest') != evidence_manifest_name(stored):
            raise EvidenceAccessError('card evidence manifest mismatch')
        read_evidence_manifest(stored, settings.evidence_dir)
        path = resolve_named_evidence(stored, settings.evidence_dir)
    except (PlacementReportError, EvidenceAccessError) as exc:
        raise HTTPException(status_code=404, detail='card evidence unavailable') from exc
    return FileResponse(path, media_type='image/png')


@router.get('/status/cycles')
def monitoring_cycles_status(limit: int = 20, db: Session = Depends(get_db)):
    return recent_monitoring_cycles(db, limit=limit)


@router.post('/cycles/{cycle_id}/retry')
def retry_monitoring_cycle(
    cycle_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Start an explicit fresh retry through the same visible-browser guard."""
    require_roles(request, 'admin', 'operator')
    try:
        result = MonitoringCycleService(db).retry(cycle_id)
    except (CycleConfigurationError, ScanConfigurationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CycleLedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'cycle': result['cycle'], 'retry_of_cycle_id': cycle_id}


@router.post('/cycles/recover-open')
def recover_open_monitoring_cycles(request: Request, db: Session = Depends(get_db)):
    """Terminally record cycles interrupted after their ledger entry was created."""
    actor = require_roles(request, 'admin', 'operator')
    try:
        return MonitoringCycleService(db).recover_open_cycles(actor=actor.username)
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/status/scans/progress')
def current_scan_progress():
    return read_scan_progress(settings.evidence_dir)


@router.get('/dashboard', response_class=HTMLResponse)
def dashboard_html(request: Request, days: int = 7, source: str = '', brand: str = '',
                   q: str = '', scope: str = 'active', db: Session = Depends(get_db)):
    safe_days = min(max(days, 1), 90)
    if source not in ('', 'auto_ru', 'avito') or scope not in ('active', 'all'):
        raise HTTPException(status_code=422, detail='unsupported dashboard filter')
    context = dashboard_context(db, days=safe_days)
    context['workspace'] = analytics_context(db, days=safe_days, source=source, brand=brand, q=q, scope=scope)
    return templates.TemplateResponse(
        request=request,
        name='dashboard.html',
        context={
            'context': context,
            'auth_enabled': settings.auth_enabled,
        },
    )


@router.get('/public/report', response_class=HTMLResponse)
def public_report_html(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name='public_report.html',
        context={'data': public_report_context(db)},
    )


@router.get('/dashboard/settings', response_class=HTMLResponse)
def settings_html(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request=request, name='operations.html', context={
        'context': dashboard_context(db), 'auth_enabled': settings.auth_enabled,
    })


@router.get('/dashboard/reconciliation', response_class=HTMLResponse)
def reconciliation_html(request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    data = reconciliation_context(db)
    data['can_create_feedback'] = can_create_feedback(actor.role)
    data['actor_name'] = actor.username
    data['actor_role'] = actor.role
    return templates.TemplateResponse(request=request, name='reconciliation.html', context={'data': data})


@router.get('/dashboard/placement-identity', response_class=HTMLResponse)
def placement_identity_html(
    request: Request, cycle_id: str | None = None, db: Session = Depends(get_db),
):
    require_roles(request, 'admin', 'operator', 'marketing', 'sales_director')
    recent = db.query(MonitoringCycle).order_by(
        MonitoringCycle.started_at.desc(), MonitoringCycle.id.desc()
    ).limit(20).all()
    available = [item for item in recent
                 if isinstance(item.summary, dict)
                 and isinstance(item.summary.get('placement_reconciliation'), dict)]
    cycle = db.get(MonitoringCycle, cycle_id) if cycle_id else (
        available[0] if available else recent[0] if recent else None
    )
    if cycle_id and cycle is None:
        raise HTTPException(status_code=404, detail='monitoring cycle not found')
    data = {
        'cycle': cycle, 'latest_cycle': recent[0] if recent else None,
        'available_cycles': available,
        'report': None, 'review': [], 'error': None,
    }
    if cycle is not None:
        try:
            report = read_cycle_placement_report(cycle, settings.evidence_dir)
        except PlacementReportError as exc:
            data['error'] = str(exc)
        else:
            evidence_by_url = {
                card.get('url'): index
                for index, card in enumerate(report['observed_cards'])
                if isinstance(card.get('url'), str) and card.get('evidence')
            }
            for finding in report['findings']:
                if not isinstance(finding, dict) or finding.get('code') in {'link_current', 'not_verified'}:
                    continue
                urls = [
                    url for url in finding.get('observed_urls', [])
                    if isinstance(url, str) and (
                        is_marketplace_listing_url(EngineType.AUTO_RU, url)
                        or is_marketplace_listing_url(EngineType.AVITO, url)
                    )
                ]
                label, action = PLACEMENT_REVIEW_LABELS.get(
                    finding.get('code'),
                    ('Нужна проверка ID', 'Сверьте карточку и фид вручную.'),
                )
                data['review'].append({
                    'finding': finding,
                    'label': label,
                    'action': action,
                    'urls': urls,
                    'evidence_index': next((evidence_by_url[url] for url in urls
                                            if url in evidence_by_url), None),
                })
            data['report'] = report
    return templates.TemplateResponse(
        request=request, name='placement_identity.html', context={'data': data},
    )


@router.get('/reconciliation/review-queue')
def reconciliation_review_queue(db: Session = Depends(get_db)):
    return offer_review_queue(db)


@router.get('/reconciliation/exceptions-report')
def reconciliation_exceptions_report(db: Session = Depends(get_db)):
    return offer_exception_report(db)


@router.get('/dashboard/company-site', response_class=HTMLResponse)
def company_site_html(request: Request, db: Session = Depends(get_db)):
    """Display the latest host-side A1Auto catalogue reconciliation."""
    return templates.TemplateResponse(
        request=request,
        name='company_site.html',
        context={'data': company_site_audit_context(), 'context': dashboard_context(db)},
    )


@router.post('/dealer/reconciliation/{check_id}/confirm')
def confirm_reconciliation(
    check_id: str,
    payload: ReconciliationConfirm,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = require_roles(request, 'admin', 'operator')
    if not settings.auth_enabled and not (payload.actor or '').strip():
        raise HTTPException(status_code=422, detail='actor is required when authentication is disabled')
    try:
        with cycle_lock(db):
            record = db.get(ListingReconciliation, check_id)
            if record is None:
                raise HTTPException(status_code=404, detail='Сверка не найдена')
            latest = db.query(ListingReconciliation).filter_by(listing_id=record.listing_id, source=record.source).order_by(
                ListingReconciliation.checked_at.desc(), ListingReconciliation.id.desc()).first()
            listing = db.get(Listing, record.listing_id)
            current = listing.source_auto_ru if record.source == EngineType.AUTO_RU else listing.source_avito
            if latest.id != record.id or canonical_listing_key(record.source, current) != canonical_listing_key(record.source, record.url):
                raise HTTPException(status_code=409, detail='Сверка или ссылка уже изменилась. Обновите страницу.')
            checked_at = record.checked_at.replace(tzinfo=datetime.UTC) if record.checked_at.tzinfo is None else record.checked_at
            if datetime.datetime.now(datetime.UTC) - checked_at > datetime.timedelta(hours=24):
                raise HTTPException(status_code=409, detail='Сверка старше суток. Повторите мониторинг перед подтверждением.')
            if not listing.is_active or payload.url not in {c['url'] for c in record.candidates}:
                raise HTTPException(status_code=422, detail='Выберите кандидата из этой сверки для активного автомобиля')
            for other in db.query(Listing).filter(Listing.is_active.is_(True), Listing.id != listing.id):
                other_url = other.source_auto_ru if record.source == EngineType.AUTO_RU else other.source_avito
                if canonical_listing_key(record.source, other_url) == canonical_listing_key(record.source, payload.url):
                    raise HTTPException(status_code=409, detail='Это объявление уже связано с другим активным автомобилем')
            ListingRegistryService(db).update_link(
                listing.id,
                source=record.source,
                url=payload.url,
                actor=actor.username if settings.auth_enabled else payload.actor.strip(),
                reason=f'Сверка {record.id}: {payload.reason}',
            )
            db.commit()
            return {'status': 'saved', 'listing_id': listing.id}
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail='Дождитесь окончания текущего мониторинга') from exc
    except ListingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/dashboard/placements', response_class=HTMLResponse)
@router.get('/dashboard/analytics', response_class=HTMLResponse)
def analytics_html(request: Request, days: int = 7, source: str = '', brand: str = '',
                   q: str = '', status: str = '', scope: str = 'active', db: Session = Depends(get_db)):
    if source not in ('', 'auto_ru', 'avito') or scope not in ('active', 'all') or (status and status not in STATES):
        raise HTTPException(status_code=422, detail='unsupported analytics filter')
    name = 'placements.html' if request.url.path.endswith('/placements') else 'analytics.html'
    return templates.TemplateResponse(request=request, name=name, context={
        'data': analytics_context(db, days=days, source=source, brand=brand, q=q, status=status, scope=scope),
    })


@router.get('/dashboard/activity', response_class=HTMLResponse)
def activity_html(request: Request, days: int = 30, kind: str = '', page: int = 1, db: Session = Depends(get_db)):
    if kind not in ('', 'registry', 'import', 'scan', 'feedback'):
        raise HTTPException(status_code=422, detail='unsupported activity kind')
    return templates.TemplateResponse(request=request, name='activity.html', context={
        'data': activity_context(db, days=days, kind=kind, page=page),
    })


@router.get('/dashboard/feedback', response_class=HTMLResponse)
def feedback_queue_html(
    request: Request,
    status: str = 'open',
    severity: str | None = None,
    category: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    actor = current_actor(request)
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
                role=actor.role,
            ),
            'auth_enabled': settings.auth_enabled,
            'actor_name': actor.username,
            'actor_role': actor.role,
            'actor_role_label': ROLE_LABELS.get(actor.role, actor.role),
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


@router.get('/observations/{observation_id}/evidence/{page_number}/manifest')
def observation_evidence_manifest(
    observation_id: str,
    page_number: int,
    db: Session = Depends(get_db),
):
    observation = db.get(ListingObservation, observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail='observation not found')
    try:
        image = resolve_observation_evidence(observation, page_number, settings.evidence_dir)
        return read_evidence_manifest(image.name, settings.evidence_dir)
    except EvidenceAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/reconciliations/{reconciliation_id}/evidence')
def reconciliation_evidence(
    reconciliation_id: str,
    db: Session = Depends(get_db),
):
    record = db.get(ListingReconciliation, reconciliation_id)
    if record is None:
        raise HTTPException(status_code=404, detail='reconciliation not found')
    direct = (record.details or {}).get('direct_inspection') or {}
    try:
        path = resolve_named_evidence(direct.get('evidence'), settings.evidence_dir)
    except EvidenceAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type='image/png',
        filename=path.name,
        content_disposition_type='inline',
    )


@router.get('/reconciliations/{reconciliation_id}/evidence/manifest')
def reconciliation_evidence_manifest(
    reconciliation_id: str,
    db: Session = Depends(get_db),
):
    record = db.get(ListingReconciliation, reconciliation_id)
    if record is None:
        raise HTTPException(status_code=404, detail='reconciliation not found')
    direct = (record.details or {}).get('direct_inspection') or {}
    try:
        return read_evidence_manifest(direct.get('evidence'), settings.evidence_dir)
    except EvidenceAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/scan', response_model=TriggerScanResponse)
def trigger_scan(request: Request, db: Session = Depends(get_db)):
    require_roles(request, 'admin', 'operator')
    service = MonitoringCycleService(db)
    started_at = datetime.datetime.now(datetime.UTC)
    try:
        summary = service.run()['scan']
    except ScanConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TriggerScanResponse(status='ok', started_at=started_at, summary=summary)


@router.post('/cycle', response_model=TriggerCycleResponse)
def trigger_cycle(request: Request, db: Session = Depends(get_db)):
    require_roles(request, 'admin', 'operator')
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
def discover_dealer_listings(request: Request, db: Session = Depends(get_db)):
    require_roles(request, 'admin', 'operator')
    try:
        require_macos_primary_monitoring_profile()
        require_local_browser_vpn_admission()
        return DealerDiscoveryService(db).run()
    except ScanConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@router.get('/dealer/candidates/{candidate_id}/evidence')
def dealer_candidate_evidence(candidate_id: str, db: Session = Depends(get_db)):
    candidate = db.get(DealerListingCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail='dealer candidate not found')
    raw = candidate.raw_payload or {}
    try:
        path = resolve_named_evidence(raw.get('page_evidence'), settings.evidence_dir)
        read_evidence_manifest(path.name, settings.evidence_dir)
    except EvidenceAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type='image/png',
        filename=path.name,
        content_disposition_type='inline',
    )


@router.post('/import', response_model=ImportResponse)
def import_source(
    request: Request,
    file_path: str | None = None,
    db: Session = Depends(get_db),
):
    require_roles(request, 'admin', 'operator')
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
def sync_canonical_filter_catalog(request: Request, db: Session = Depends(get_db)):
    require_roles(request, 'admin', 'operator')
    return FilterRegistryService(db).sync_canonical_catalog()


@router.post('/filters')
def upsert_filter(
    payload: FilterUpsert,
    request: Request,
    db: Session = Depends(get_db),
):
    require_roles(request, 'admin', 'operator')
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
    filter_id: str,
    payload: FilterStateChange,
    request: Request,
    db: Session = Depends(get_db),
):
    require_roles(request, 'admin', 'operator')
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
    listing_id: str,
    payload: ListingLinkUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = require_roles(request, 'admin', 'operator')
    try:
        with cycle_lock(db):
            listing = ListingRegistryService(db).update_link(
                listing_id,
                source=payload.source,
                url=payload.url,
                actor=actor.username if settings.auth_enabled else payload.actor,
                reason=payload.reason,
            )
            db.commit()
    except ScanAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail='Дождитесь окончания текущего мониторинга') from exc
    except ListingValidationError as exc:
        code = 404 if str(exc) == 'listing not found' else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {
        'id': listing.id,
        'auto_ru_url': listing.source_auto_ru,
        'avito_url': listing.source_avito,
    }


@router.post('/feedback')
def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = current_actor(request)
    if not can_create_feedback(actor.role):
        raise HTTPException(status_code=403, detail='your role cannot create feedback')
    service = FeedbackService(db)
    try:
        feedback_id = service.create(
            message=payload.message,
            listing_id=payload.listing_id,
            filter_id=payload.filter_id,
            observed_id=payload.observed_id,
            severity=payload.severity,
            category=payload.category,
            manager_name=actor.username if settings.auth_enabled else payload.manager_name,
            source=payload.source,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'feedback_id': feedback_id, 'status': 'created', 'actor': actor.username, 'role': actor.role}


@router.post('/reconciliation/{reconciliation_id}/feedback')
def create_reconciliation_feedback(
    reconciliation_id: str,
    payload: OfferFindingFeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Open a ticket only for a current, explainable M4 finding."""
    actor = current_actor(request)
    if not can_create_feedback(actor.role):
        raise HTTPException(status_code=403, detail='your role cannot create feedback')
    finding = next(
        (
            item
            for item in offer_review_queue(db)['findings']
            if item.get('reconciliation_id') == reconciliation_id
            and item.get('code') == payload.finding_code
        ),
        None,
    )
    if finding is None:
        raise HTTPException(
            status_code=409,
            detail='finding is no longer current; refresh reconciliation before creating feedback',
        )
    category = {
        'price_mismatch': 'price_error',
        'vin_mismatch': 'data_error',
        'year_mismatch': 'data_error',
        'vat_not_disclosed': 'description_error',
        'vat_ambiguous': 'description_error',
        'missing_offer': 'visibility_error',
        'missing_offer_link': 'visibility_error',
        'replacement_candidate': 'visibility_error',
        'offer_not_verified_in_catalogue': 'visibility_error',
    }.get(payload.finding_code, 'other')
    try:
        feedback_id = FeedbackService(db).create(
            message=payload.message,
            reconciliation_id=reconciliation_id,
            finding_code=payload.finding_code,
            severity=finding['severity'],
            category=category,
            manager_name=actor.username,
            source=finding['source'],
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        'feedback_id': feedback_id,
        'status': 'created',
        'finding_code': payload.finding_code,
        'actor': actor.username,
        'role': actor.role,
    }


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
def update_feedback(
    feedback_id: str,
    payload: FeedbackUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = current_actor(request)
    item = db.get(ManagerFeedback, feedback_id)
    if item is None:
        raise HTTPException(status_code=404, detail='feedback not found')
    try:
        target = FeedbackStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='unsupported feedback status') from exc
    if not can_transition_feedback(role=actor.role, current=item.status, target=target):
        raise HTTPException(status_code=403, detail='your role cannot perform this feedback transition')
    try:
        item = FeedbackService(db).update_status(
            feedback_id,
            payload.status,
            actor=actor.username if settings.auth_enabled else (payload.actor or ''),
            note=payload.note,
            assignee=payload.assignee,
        )
    except FeedbackValidationError as exc:
        code = 404 if str(exc) == 'feedback not found' else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {
        'id': item.id,
        'status': item.status.value,
        'assignee': item.assignee,
        'actor': actor.username,
        'role': actor.role,
    }


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
