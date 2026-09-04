from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.importer.service import SourceImporter
from app.importer.sheet_csv import CsvOrXlsxReader
from app.service.evidence import cleanup_evidence
from app.service.monitor import MonitorService


class CycleConfigurationError(RuntimeError):
    pass


class MonitoringCycleService:
    def __init__(self, db: Session):
        self.db = db

    def run(self) -> dict:
        source_path = settings.source_google_sheet_export_url or settings.source_csv_path
        if not source_path:
            raise CycleConfigurationError('source import URL or path is not configured')
        evidence_removed = cleanup_evidence(
            settings.evidence_dir, settings.report_retention_days
        )
        rows = CsvOrXlsxReader(source_path).read()
        imported = SourceImporter(self.db).run(rows, source_signature=source_path)
        scanned = MonitorService(self.db).run_full_cycle()
        return {
            'import': imported,
            'scan': scanned,
            'evidence_removed': evidence_removed,
        }
