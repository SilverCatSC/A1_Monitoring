from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from app.config import SCAN_ALLOWED_NETWORK_PROFILES, settings
from app.importer.service import SourceImporter
from app.importer.sheet_csv import CsvOrXlsxReader
from app.service.automatic_link_sync import AutomaticLinkSyncService
from app.service.completion import summarize_cycle_completion
from app.service.cycle_ledger import CycleLedgerService
from app.service.evidence import cleanup_evidence
from app.service.filters import FilterRegistryService
from app.service.host_runner_context import (
    HostRunnerContextError,
    require_verified_macos_host_runner_context,
)
from app.service.locks import operation_lock
from app.service.monitor import (
    MonitorService,
    ScanAlreadyRunning,
    ScanConfigurationError,
    validate_scan_sources,
)
from app.service.placement_cycle import PlacementCycleService
from app.service.reconciliation import SellerReconciliationService
from app.service.vpn_admission import VPNAdmissionError, require_operational_vpn_admission


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


def require_local_browser_vpn_admission() -> None:
    """Require owner policy plus connected VPSUS before a local Chrome cycle.

    Operational readiness does not claim verified per-domain egress or M7
    acceptance.  The verifier does not change or reconnect VPSUS.
    """
    if settings.network_profile != 'local_browser':
        return
    if not settings.local_browser_host_admission:
        raise ScanConfigurationError(
            'local_browser monitoring must be started by the approved interactive host runner'
        )
    try:
        require_verified_macos_host_runner_context()
    except HostRunnerContextError as exc:
        raise ScanConfigurationError(
            'local_browser monitoring must be started by the approved interactive host runner'
        ) from exc
    try:
        require_operational_vpn_admission(settings.vpn_operational_policy_path)
    except VPNAdmissionError as exc:
        raise ScanConfigurationError(
            'local_browser monitoring requires an approved VPN operational policy '
            'and connected VPSUS'
        ) from exc


def require_macos_primary_monitoring_profile() -> None:
    """Admit marketplace work only from the selected MacBook execution model.

    Legacy profile labels remain readable for historical reporting, but they
    must not silently become a new browser worker after the owner selected the
    MacBook interactive host as the sole accepted production runtime.
    """
    if settings.network_profile != 'local_browser':
        raise ScanConfigurationError(
            'MacBook primary monitoring requires NETWORK_PROFILE=local_browser; '
            f'current={settings.network_profile}'
        )


class MonitoringCycleService:
    def __init__(self, db: Session, progress_callback=None, before_browser=None):
        self.db = db
        self.progress = progress_callback or (lambda event: None)
        self.before_browser = before_browser

    def run(self, *, retry_of_cycle_id: str | None = None) -> dict:
        require_macos_primary_monitoring_profile()
        if settings.network_profile not in SCAN_ALLOWED_NETWORK_PROFILES:
            raise ScanConfigurationError(f'scan requires a trusted local profile; current={settings.network_profile}')
        if not settings.browser_cdp_url:
            raise ScanConfigurationError('Полный мониторинг требует видимого локального Chrome. Используйте scripts/local_scan.sh.')
        validate_scan_sources()
        require_local_browser_vpn_admission()
        with cycle_lock(self.db):
            ledger = CycleLedgerService(self.db)
            cycle = (
                ledger.start(retry_of_cycle_id=retry_of_cycle_id)
                if retry_of_cycle_id
                else ledger.start()
            )
            self.progress({'event': 'cycle_registered', 'cycle_id': cycle.id})
            try:
                result = self._run(cycle.id, ledger)
            except (Exception, KeyboardInterrupt) as exc:
                ledger.fail(cycle.id, f'{type(exc).__name__}: {exc}')
                self.progress({'event': 'cycle_failed', 'error': f'{type(exc).__name__}: {exc}'})
                raise
            cycle_summary = ledger.complete(cycle.id, result['completion'])
            return {**result, 'cycle': cycle_summary}

    def recover_open_cycles(self, *, actor: str = 'local_cli') -> dict:
        """Safely finalize interrupted ledger rows without starting a scan.

        The same lock used by a complete cycle proves that a concurrent runner
        cannot be mistaken for an abandoned one. No marketplace, source import,
        browser, or evidence operation occurs here.
        """
        with cycle_lock(self.db):
            recovered = CycleLedgerService(self.db).recover_open_cycles(actor=actor)
        if recovered:
            self.progress({'event': 'open_cycles_recovered', 'recovered': recovered})
        return {'recovered': recovered, 'count': len(recovered)}

    def retry(self, cycle_id: str) -> dict:
        """Retry as a new cycle, retaining only parent provenance.

        Marketplace and source facts can change between attempts. Reusing a
        failed cycle's roster or adding new observations to its manifest would
        make the evidence ambiguous, so this always starts a fresh cycle.
        """
        CycleLedgerService(self.db).ensure_retryable(cycle_id)
        return self.run(retry_of_cycle_id=cycle_id)

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
        if self.before_browser:
            self.before_browser()
        reconciliation = SellerReconciliationService(
            self.db, progress_callback=self.progress, cycle_id=cycle_id
        )
        preflight = reconciliation.run()
        placement = None
        link_sync = None
        if settings.placement_reconciliation_enabled:
            self.progress({'event': 'link_preflight_started'})
            placement = PlacementCycleService(
                self.db, progress_callback=self.progress, cycle_id=cycle_id
            ).run(preflight, settings.placement_feed_workbook_url
                  or settings.head_table_google_sheet_export_url)
            link_sync = AutomaticLinkSyncService(self.db, cycle_id=cycle_id).run(preflight, placement)
            self.progress({'event': 'link_sync_finished', 'status': link_sync['status'],
                           'updated': len(link_sync['updated']), 'blocked': len(link_sync['blocked'])})
            preflight['blocked_sources'] = sorted(
                set(preflight.get('blocked_sources', []))
                | set(placement.get('blocked_sources', []))
            )
        # The immutable monitoring roster is sealed after URL synchronization,
        # and before the first search observation. The import snapshot and the
        # old-link checks remain separate, auditable preparation evidence.
        manifest = ledger.seal_roster(cycle_id, refreshed['import'].get('snapshot_id'))
        self.progress({
            'event': 'cycle_roster_sealed',
            'cycle_id': cycle_id,
            'roster_count': manifest['roster_count'],
        })
        # Incomplete catalogue coverage must not suppress search for listings
        # whose current seller link was verified. MonitorService gates each
        # expectation individually; unresolved links become REVIEW_REQUIRED.
        # Only an unavailable ID stage stops the whole search.
        link_stage_available = placement is None or (
            placement['status'] in {'complete', 'partial'}
            and link_sync['status'] in {'complete', 'partial'}
        )
        if link_stage_available:
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
        else:
            scanned = {'status': 'skipped', 'reason': 'link_reconciliation_incomplete'}
            direct_cards = {'status': 'skipped', 'reason': 'link_reconciliation_incomplete'}
            completion = {
                'status': 'partial', 'technical_errors': 0,
                'partial_reasons': ['link_reconciliation_incomplete'],
                'search_skipped': True,
            }
        if placement is not None:
            completion['placement_reconciliation'] = placement
            completion['automatic_link_sync'] = link_sync
            if placement['status'] != 'complete':
                completion['status'] = 'partial'
                completion['partial_reasons'].append('placement_reconciliation_incomplete')
            if link_sync['status'] != 'complete':
                completion['status'] = 'partial'
                completion['partial_reasons'].append('automatic_link_sync_incomplete')
        self.progress({'event': 'cycle_completed', 'summary': completion})
        result = {
            **refreshed,
            'dealer_discovery': preflight['discovery'],
            'seller_preflight': {'batch_id': preflight['batch_id'], **preflight['summary']},
            'scan': scanned,
            'direct_cards': direct_cards,
            'completion': completion,
            'manifest': manifest,
            'evidence_removed': evidence_removed,
        }
        if placement is not None:
            result['placement_reconciliation'] = placement
        return result
