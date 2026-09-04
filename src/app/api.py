from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.importer.service import SourceImporter, SourceImportError
from app.importer.sheet_csv import CsvOrXlsxReader
from app.models import AbsenceEpisode, SearchFilter, SourceImportSnapshot
from app.schemas import FeedbackCreate, HealthResponse, ImportResponse, KPIResponse, TriggerScanResponse
from app.service.feedback import FeedbackService
from app.service.monitor import MonitorService
from app.service.report import kpi_overview

router = APIRouter()


@router.get('/health', response_model=HealthResponse)
def health():
    return HealthResponse(
        status='ok',
        timestamp=datetime.datetime.utcnow(),
        app_version=settings.app_version,
    )


@router.get('/ready')
def ready():
    return {'status': 'ready'}


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


@router.get('/dashboard', response_class=HTMLResponse)
def dashboard_html(days: int = 7, db: Session = Depends(get_db)):
    kpi = kpi_overview(db, days=days)
    missing = (
        db.query(AbsenceEpisode)
        .filter(AbsenceEpisode.open.is_(True))
        .order_by(AbsenceEpisode.started_at.desc())
        .limit(200)
        .all()
    )
    rows_html = ''.join(
        f'<tr><td>{item.listing_id}</td><td>{item.filter_id}</td><td>{item.source.value}</td>'
        f'<td>{item.started_at}</td><td>{item.consecutive_misses}</td></tr>'
        for item in missing
    )
    return f"""
    <html>
      <head>
        <meta charset='utf-8' />
        <title>Monitoring Dashboard</title>
      </head>
      <body>
        <h1>Мониторинг A1 Search</h1>
        <p>Найдено: {kpi['observed_found']}</p>
        <p>Отсутствует: {kpi['observed_missed']}</p>
        <p>Успешные прогоны: {kpi['scan_runs_success']}</p>
        <p>Открытых эпизодов отсутствия: {kpi['absent_active']}</p>
        <h2>Текущие пропуски</h2>
        <table border='1'>
          <thead><tr><th>Listing</th><th>Filter</th><th>Источник</th><th>Начало</th><th>Пропуски подряд</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </body>
    </html>
    """


@router.post('/scan', response_model=TriggerScanResponse)
def trigger_scan(db: Session = Depends(get_db)):
    service = MonitorService(db)
    started_at = datetime.datetime.utcnow()
    try:
        summary = service.run_full_cycle()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TriggerScanResponse(status='ok', started_at=started_at, summary=summary)


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
    except SourceImportError as exc:
        # keep last-good snapshot and still expose drift diagnostics
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ImportResponse(**summary)


@router.get('/filters')
def list_filters(db: Session = Depends(get_db)):
    filters = db.query(SearchFilter).order_by(SearchFilter.name.asc()).all()
    return {
        'filters': [
            {'id': f.id, 'name': f.name, 'source': f.source.value, 'active': f.active} for f in filters
        ]
    }


@router.post('/feedback')
def create_feedback(payload: FeedbackCreate, db: Session = Depends(get_db)):
    service = FeedbackService(db)
    feedback_id = service.create(
        message=payload.message,
        listing_id=payload.listing_id,
        filter_id=payload.filter_id,
        severity=payload.severity,
        manager_name=payload.manager_name,
        source=payload.source,
    )
    return {'feedback_id': feedback_id, 'status': 'created'}


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
