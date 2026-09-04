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

    monkeypatch.setattr(cycle_module.settings, 'source_google_sheet_export_url', 'https://sheet.csv')
    monkeypatch.setattr(cycle_module, 'CsvOrXlsxReader', Reader)
    monkeypatch.setattr(cycle_module, 'SourceImporter', Importer)
    monkeypatch.setattr(cycle_module, 'MonitorService', Monitor)
    monkeypatch.setattr(cycle_module, 'cleanup_evidence', lambda *_: 3)

    result = cycle_module.MonitoringCycleService('db').run()

    assert [event[0] for event in events] == ['reader', 'read', 'import', 'scan']
    assert result == {
        'import': {'rows_valid': 1},
        'scan': {'runs': 2},
        'evidence_removed': 3,
    }
