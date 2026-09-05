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
        def __init__(self, db):
            assert db == 'db'

        def run_full_cycle(self):
            events.append(('scan', None))
            return {'runs': 2}

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
        def __init__(self, db):
            assert db == 'db'

        def run(self):
            events.append(('discover', None))
            return {'complete': 2}

    monkeypatch.setattr(cycle_module.settings, 'source_google_sheet_export_url', 'https://sheet.csv')
    monkeypatch.setattr(cycle_module, 'CsvOrXlsxReader', Reader)
    monkeypatch.setattr(cycle_module, 'SourceImporter', Importer)
    monkeypatch.setattr(cycle_module, 'MonitorService', Monitor)
    monkeypatch.setattr(cycle_module, 'FilterRegistryService', Filters)
    monkeypatch.setattr(cycle_module, 'DealerDiscoveryService', Discovery)
    monkeypatch.setattr(cycle_module.settings, 'dealer_discovery_enabled', True)
    monkeypatch.setattr(cycle_module, 'cleanup_evidence', lambda *_: 3)

    result = cycle_module.MonitoringCycleService('db').run()

    assert [event[0] for event in events] == [
        'reader',
        'read',
        'import',
        'refresh',
        'catalog',
        'discover',
        'scan',
    ]
    assert result == {
        'import': {'rows_valid': 1},
        'filter_assignments': {'managed_filters': 1, 'added': 2},
        'canonical_filters': {'definitions': 16, 'expectations': 7},
        'dealer_discovery': {'complete': 2},
        'scan': {'runs': 2},
        'evidence_removed': 3,
    }
