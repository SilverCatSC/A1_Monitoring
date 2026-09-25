def test_cycle_imports_before_scan(monkeypatch):
    import app.service.cycle as cycle_module

    events = []

    class Ledger:
        def __init__(self, db):
            assert db == 'db'

        def start(self):
            return type('Cycle', (), {'id': 'cycle-1'})()

        def seal_roster(self, cycle_id, snapshot_id):
            assert (cycle_id, snapshot_id) == ('cycle-1', 'snapshot-1')
            return {
                'id': cycle_id,
                'roster_count': 1,
                'roster_sha256': 'roster-hash',
                'manifest_path': 'cycles/cycle-1/manifest.json',
            }

        def complete(self, cycle_id, summary):
            assert cycle_id == 'cycle-1'
            assert summary['status'] == 'completed'
            return {'id': cycle_id, 'status': 'completed', 'manifest_path': 'cycles/cycle-1/manifest.json'}

        def fail(self, *_):
            raise AssertionError('successful cycle must not fail')

    class Reader:
        def __init__(self, source):
            events.append(('reader', source))

        def read(self):
            events.append(('read', None))
            return ['row']

    class Importer:
        def __init__(self, db):
            assert db == 'db'

        def run(self, rows, source_signature, cycle_id=None):
            assert cycle_id == 'cycle-1'
            events.append(('import', rows, source_signature))
            return {'rows_valid': 1, 'snapshot_id': 'snapshot-1'}

    class Monitor:
        def __init__(self, db, progress_callback=None, preflight=None, cycle_id=None):
            assert db == 'db'
            assert preflight['batch_id'] == 'batch'
            assert cycle_id == 'cycle-1'

        def run_full_cycle(self):
            events.append(('scan', None))
            return {'runs': 2, 'blocked_sources': ['avito']}

    class Filters:
        def __init__(self, db):
            assert db == 'db'

        def refresh_managed_assignments(self):
            events.append(('refresh', None))
            return {'managed_filters': 1, 'added': 2}

        def sync_canonical_catalog(self):
            events.append(('catalog', None))
            return {'definitions': 16, 'expectations': 7}

    class Discovery:
        def __init__(self, db, progress_callback=None, cycle_id=None):
            assert db == 'db'
            assert cycle_id == 'cycle-1'

        def run(self):
            events.append(('discover', None))
            return {'discovery': {'complete': 2}, 'batch_id': 'batch', 'summary': {'verified': 1}}

        def inspect_current_cards(self, preflight):
            events.append(('direct_cards', preflight['batch_id']))
            assert preflight['blocked_sources'] == ['avito']
            return {'checked': 1, 'total': 1}

    monkeypatch.setattr(cycle_module.settings, 'source_google_sheet_export_url', 'https://sheet.csv')
    monkeypatch.setattr(cycle_module, 'CsvOrXlsxReader', Reader)
    monkeypatch.setattr(cycle_module, 'SourceImporter', Importer)
    monkeypatch.setattr(cycle_module, 'MonitorService', Monitor)
    monkeypatch.setattr(cycle_module, 'FilterRegistryService', Filters)
    monkeypatch.setattr(cycle_module, 'SellerReconciliationService', Discovery)
    monkeypatch.setattr(cycle_module, 'CycleLedgerService', Ledger)
    monkeypatch.setattr(cycle_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(cycle_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(cycle_module.settings, 'local_browser_host_admission', True)
    monkeypatch.setattr(cycle_module, 'require_verified_macos_host_runner_context', lambda: None)
    monkeypatch.setattr(cycle_module, 'require_operational_vpn_admission', lambda _path: None)
    monkeypatch.setattr(cycle_module.settings, 'dealer_discovery_enabled', True)
    monkeypatch.setattr(cycle_module, 'cleanup_evidence', lambda *_: 3)

    result = cycle_module.MonitoringCycleService('db', before_browser=lambda: events.append(('chrome', None))).run()

    assert [event[0] for event in events] == [
        'reader',
        'read',
        'import',
        'refresh',
        'catalog',
        'chrome',
        'discover',
        'scan',
        'direct_cards',
    ]
    assert result == {
        'import': {'rows_valid': 1, 'snapshot_id': 'snapshot-1'},
        'filter_assignments': {'managed_filters': 1, 'added': 2},
        'canonical_filters': {'definitions': 16, 'expectations': 7},
        'dealer_discovery': {'complete': 2},
        'seller_preflight': {'batch_id': 'batch', 'verified': 1},
        'scan': {'runs': 2, 'blocked_sources': ['avito']},
        'direct_cards': {'checked': 1, 'total': 1},
        'completion': {
            'status': 'completed',
            'technical_errors': 0,
            'search_technical_errors': 0,
            'direct_cards_technical_errors': 0,
            'links_need_review': 0,
            'direct_cards_incomplete': 0,
            'partial_reasons': [],
        },
        'manifest': {
            'id': 'cycle-1',
            'roster_count': 1,
            'roster_sha256': 'roster-hash',
            'manifest_path': 'cycles/cycle-1/manifest.json',
        },
        'cycle': {
            'id': 'cycle-1',
            'status': 'completed',
            'manifest_path': 'cycles/cycle-1/manifest.json',
        },
        'evidence_removed': 3,
    }


def test_source_refresh_requires_a_configured_monitoring_table(monkeypatch):
    import pytest

    import app.service.cycle as cycle_module

    monkeypatch.setattr(cycle_module.settings, 'source_google_sheet_export_url', None)
    monkeypatch.setattr(cycle_module.settings, 'source_csv_path', None)

    with pytest.raises(cycle_module.CycleConfigurationError, match='source import URL'):
        cycle_module.refresh_monitoring_source('db')


def test_placement_stage_is_in_cycle_and_incomplete_coverage_keeps_partial(monkeypatch):
    import app.service.cycle as cycle_module

    class Ledger:
        def seal_roster(self, _cycle_id, _snapshot_id):
            return {'roster_count': 1}

    class Reconciliation:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            return {'batch_id': 'batch', 'summary': {}, 'discovery': {'run_ids': ['run']},
                    'blocked_sources': []}

        def inspect_current_cards(self, _preflight):
            return {'total': 0, 'incomplete': 0, 'technical_errors': 0}

    class Monitor:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_full_cycle(self):
            return {'technical_errors': 0, 'links_need_review': 0, 'blocked_sources': []}

    class Placement:
        def __init__(self, _db, *, progress_callback, cycle_id):
            assert cycle_id == 'cycle-1'

        def run(self, preflight, source_url):
            assert preflight['batch_id'] == 'batch'
            assert source_url == 'https://docs.google.com/spreadsheets/d/abc/export'
            return {'status': 'partial', 'findings': 1, 'report_path': 'cycles/cycle-1/report.json'}

    monkeypatch.setattr(cycle_module.settings, 'placement_reconciliation_enabled', True)
    monkeypatch.setattr(cycle_module.settings, 'placement_feed_workbook_url',
                        'https://docs.google.com/spreadsheets/d/abc/export')
    monkeypatch.setattr(cycle_module, 'cleanup_evidence', lambda *_args: 0)
    monkeypatch.setattr(cycle_module, 'refresh_monitoring_source',
                        lambda *_args, **_kwargs: {'import': {'snapshot_id': 'snapshot'},
                                                   'filter_assignments': {}, 'canonical_filters': {}})
    monkeypatch.setattr(cycle_module, 'SellerReconciliationService', Reconciliation)
    monkeypatch.setattr(cycle_module, 'MonitorService', Monitor)
    monkeypatch.setattr(cycle_module, 'PlacementCycleService', Placement)

    result = cycle_module.MonitoringCycleService('db')._run('cycle-1', Ledger())

    assert result['placement_reconciliation']['status'] == 'partial'
    assert result['completion']['placement_reconciliation']['findings'] == 1
    assert result['completion']['status'] == 'partial'
    assert 'placement_reconciliation_incomplete' in result['completion']['partial_reasons']


def test_cycle_rejects_invalid_source_configuration_before_import(monkeypatch):
    import pytest

    import app.service.cycle as cycle_module

    monkeypatch.setattr(cycle_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(cycle_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(cycle_module.settings, 'scan_enabled_engines', '')
    monkeypatch.setattr(cycle_module, 'refresh_monitoring_source', lambda *_args, **_kwargs: pytest.fail('must not import'))

    with pytest.raises(cycle_module.ScanConfigurationError, match='at least one'):
        cycle_module.MonitoringCycleService('db').run()


def test_cycle_lock_covers_import_and_browser_and_releases_after_error():
    import pytest

    from app.service.cycle import cycle_lock
    from app.service.monitor import ScanAlreadyRunning
    with cycle_lock('test-db'):
        with pytest.raises(ScanAlreadyRunning):
            with cycle_lock('test-db'):
                raise AssertionError('overlapping cycle entered')
    with pytest.raises(RuntimeError), cycle_lock('test-db'):
        raise RuntimeError('fixture failure')
    with cycle_lock('test-db'):
        pass


def test_failed_refresh_does_not_open_chrome_or_contact_sellers(monkeypatch):
    import pytest

    import app.service.cycle as cycle_module
    monkeypatch.setattr(cycle_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(cycle_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(cycle_module.settings, 'local_browser_host_admission', True)
    monkeypatch.setattr(cycle_module, 'require_verified_macos_host_runner_context', lambda: None)
    monkeypatch.setattr(cycle_module, 'require_operational_vpn_admission', lambda _path: None)
    monkeypatch.setattr(cycle_module, 'cleanup_evidence', lambda *_: 0)
    class Ledger:
        def __init__(self, _):
            pass

        def start(self):
            return type('Cycle', (), {'id': 'cycle-1'})()

        def fail(self, cycle_id, error):
            assert cycle_id == 'cycle-1'
            assert 'source is unavailable' in error

    def failed_refresh(_, cycle_id=None):
        assert cycle_id == 'cycle-1'
        raise cycle_module.CycleConfigurationError('source is unavailable')
    def forbidden(*_, **__):
        raise AssertionError('browser or seller must not be contacted')
    monkeypatch.setattr(cycle_module, 'refresh_monitoring_source', failed_refresh)
    monkeypatch.setattr(cycle_module, 'SellerReconciliationService', forbidden)
    monkeypatch.setattr(cycle_module, 'CycleLedgerService', Ledger)
    with pytest.raises(cycle_module.CycleConfigurationError):
        cycle_module.MonitoringCycleService('db', before_browser=forbidden).run()


def test_keyboard_interrupt_writes_a_terminal_ledger_failure(monkeypatch):
    import pytest

    import app.service.cycle as cycle_module

    failures = []

    class Ledger:
        def __init__(self, _):
            pass

        def start(self):
            return type('Cycle', (), {'id': 'cycle-1'})()

        def fail(self, cycle_id, error):
            failures.append((cycle_id, error))

    monkeypatch.setattr(cycle_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(cycle_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(cycle_module.settings, 'local_browser_host_admission', True)
    monkeypatch.setattr(cycle_module, 'require_verified_macos_host_runner_context', lambda: None)
    monkeypatch.setattr(cycle_module, 'require_operational_vpn_admission', lambda _path: None)
    monkeypatch.setattr(cycle_module, 'CycleLedgerService', Ledger)
    monkeypatch.setattr(cycle_module, 'validate_scan_sources', lambda: None)
    monkeypatch.setattr(
        cycle_module.MonitoringCycleService,
        '_run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        cycle_module.MonitoringCycleService('db').run()

    assert failures == [('cycle-1', 'KeyboardInterrupt: ')]
