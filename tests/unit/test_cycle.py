def test_cycle_imports_before_scan(monkeypatch):
    import app.service.cycle as cycle_module

    events = []

    class Reader:
        def __init__(self, source):
            events.append(('reader', source))

        def read(self):
            events.append(('read', None))
            return ['row']

    class Importer:
        def __init__(self, db):
            assert db == 'db'

        def run(self, rows, source_signature):
            events.append(('import', rows, source_signature))
            return {'rows_valid': 1}

    class Monitor:
        def __init__(self, db, progress_callback=None, preflight=None):
            assert db == 'db'
            assert preflight['batch_id'] == 'batch'

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
        def __init__(self, db, progress_callback=None):
            assert db == 'db'

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
    monkeypatch.setattr(cycle_module.settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(cycle_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
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
        'import': {'rows_valid': 1},
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
        'evidence_removed': 3,
    }


def test_source_refresh_requires_a_configured_monitoring_table(monkeypatch):
    import pytest

    import app.service.cycle as cycle_module

    monkeypatch.setattr(cycle_module.settings, 'source_google_sheet_export_url', None)
    monkeypatch.setattr(cycle_module.settings, 'source_csv_path', None)

    with pytest.raises(cycle_module.CycleConfigurationError, match='source import URL'):
        cycle_module.refresh_monitoring_source('db')


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
    monkeypatch.setattr(cycle_module, 'cleanup_evidence', lambda *_: 0)
    def failed_refresh(_):
        raise cycle_module.CycleConfigurationError('source is unavailable')
    def forbidden(*_, **__):
        raise AssertionError('browser or seller must not be contacted')
    monkeypatch.setattr(cycle_module, 'refresh_monitoring_source', failed_refresh)
    monkeypatch.setattr(cycle_module, 'SellerReconciliationService', forbidden)
    with pytest.raises(cycle_module.CycleConfigurationError):
        cycle_module.MonitoringCycleService('db', before_browser=forbidden).run()
