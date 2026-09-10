from __future__ import annotations

import argparse

from app.config import settings
from app.db import get_db_context, init_db
from app.importer.service import SourceImporter
from app.importer.sheet_csv import CsvOrXlsxReader
from app.service.cycle import MonitoringCycleService
from app.service.filters import FilterRegistryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser('a1-search-monitor')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('init')
    sub.add_parser('serve')

    import_source = sub.add_parser('import-source')
    import_source.add_argument('--path', required=False)

    sub.add_parser('scan')

    sub.add_parser('run-cycle')
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == 'init':
        init_db()
        return
    if args.command in {'serve', 'run-cycle', 'scan'}:
        import uvicorn

        from app.main import app

        if args.command == 'serve':
            uvicorn.run(app, host='0.0.0.0', port=8000)
            return
        with get_db_context() as db:
            if args.command == 'scan':
                MonitoringCycleService(db).run()
            elif args.command == 'run-cycle':
                MonitoringCycleService(db).run()
        return

    if args.command == 'import-source':
        source_path = args.path or settings.source_google_sheet_export_url or settings.source_csv_path
        if not source_path:
            raise RuntimeError('path is required for import-source or set SOURCE_CSV_PATH in .env')
        rows = CsvOrXlsxReader(source_path).read()
        with get_db_context() as db:
            SourceImporter(db).run(rows, source_signature=source_path)
            registry = FilterRegistryService(db)
            registry.refresh_managed_assignments()
            registry.sync_canonical_catalog()
        return


if __name__ == '__main__':
    main()
