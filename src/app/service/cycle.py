from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from app.config import SCAN_ALLOWED_NETWORK_PROFILES, settings
from app.importer.service import SourceImporter
from app.importer.sheet_csv import CsvOrXlsxReader
from app.service.completion import summarize_cycle_completion
from app.service.cycle_ledger import CycleLedgerService
from app.service.evidence import cleanup_evidence
from app.service.filters import FilterRegistryService
from app.service.locks import operation_lock
from app.service.monitor import (
    MonitorService,
    ScanAlreadyRunning,
    ScanConfigurationError,
    validate_scan_sources,
)
from app.service.reconciliation import SellerReconciliationService


class CycleConfigurationError(RuntimeError):
    pass


_cycle_lock = threading.Lock()


def cycle_lock(db):
    return operation_lock(db, 4101003, _cycle_lock, ScanAlreadyRunning,
                          'another complete monitoring cycle is running')


def refresh_monitoring_source(db: Session, cycle_id: str | None = None) -> dict:
    """Refresh the trusted listing registry before any marketplace observation."""
    source_path = settings.source_google_sheet_export_url or settings.source_csv_path
    if not source_path:
        raise CycleConfigurationError('source import URL or path is not configured')
    rows = CsvOrXlsxReader(source_path).read()
    imported = SourceImporter(db).run(
        rows, source_signature=source_path, cycle_id=cycle_id
    )
    registry = FilterRegistryService(db)
    assignments = registry.refresh_managed_assignments()
    canonical_filters = registry.sync_canonical_catalog()
    return {
        'import': imported,
        'filter_assignments': assignments,
        'canonical_filters': canonical_filters,
    }


class MonitoringCycleService:
    def __init__(self, db: Session, progress_callback=None, before_browser=None):
        self.db = db
        self.progress = progress_callback or (lambda event: None)
        self.before_browser = before_browser

    def run(self) -> dict:
        if settings.network_profile not in SCAN_ALLOWED_NETWORK_PROFILES:
            raise ScanConfigurationError(f'scan requires a trusted local profile; current={settings.network_profile}')
        if not settings.browser_cdp_url:
            raise ScanConfigurationError('Полный мониторинг требует видимого локального Chrome. Используйте scripts/local_scan.sh.')
        validate_scan_sources()
        with cycle_lock(self.db):
            ledger = CycleLedgerService(self.db)
            cycle = ledger.start()
            self.progress({'event': 'cycle_registered', 'cycle_id': cycle.id})
            try:
                result = self._run(cycle.id, ledger)
            except Exception as exc:
                ledger.fail(cycle.id, f'{type(exc).__name__}: {exc}')
                self.progress({'event': 'cycle_failed', 'error': f'{type(exc).__name__}: {exc}'})
                raise
            cycle_summary = ledger.complete(cycle.id, result['completion'])
            return {**result, 'cycle': cycle_summary}

    def _run(self, cycle_id: str, ledger: CycleLedgerService):
        evidence_removed = cleanup_evidence(
            settings.evidence_dir, settings.report_retention_days
        )
        self.progress({'event': 'source_refresh_started'})
        try:
            refreshed = refresh_monitoring_source(self.db, cycle_id=cycle_id)
        except Exception as exc:
            self.progress({'event': 'source_refresh_failed', 'error': f'{type(exc).__name__}: {exc}'})
            raise
        self.progress({'event': 'source_refresh_finished', **refreshed['import']})
        manifest = ledger.seal_roster(cycle_id, refreshed['import'].get('snapshot_id'))
        self.progress({
            'event': 'cycle_roster_sealed',
            'cycle_id': cycle_id,
            'roster_count': manifest['roster_count'],
        })
        if self.before_browser:
            self.before_browser()
        reconciliation = SellerReconciliationService(
            self.db, progress_callback=self.progress, cycle_id=cycle_id
        )
        preflight = reconciliation.run()
        scanned = MonitorService(
            self.db,
            progress_callback=self.progress,
            preflight=preflight,
            cycle_id=cycle_id,
        ).run_full_cycle()
        preflight['blocked_sources'] = sorted(
            set(preflight.get('blocked_sources', []))
            | set(scanned.get('blocked_sources', []))
        )
        direct_cards = reconciliation.inspect_current_cards(preflight)
        completion = summarize_cycle_completion(scanned, direct_cards)
        self.progress({'event': 'cycle_completed', 'summary': completion})
        return {
            **refreshed,
            'dealer_discovery': preflight['discovery'],
            'seller_preflight': {'batch_id': preflight['batch_id'], **preflight['summary']},
            'scan': scanned,
            'direct_cards': direct_cards,
            'completion': completion,
            'manifest': manifest,
            'evidence_removed': evidence_removed,
        }
