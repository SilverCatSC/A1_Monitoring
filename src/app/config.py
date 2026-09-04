from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_env: str = Field(default='development', alias='APP_ENV')
    app_tz: str = Field(default='Europe/Moscow', alias='APP_TZ')
    auth_enabled: bool = Field(default=False, alias='AUTH_ENABLED')
    admin_username: str | None = Field(default=None, alias='ADMIN_USERNAME')
    admin_password: str | None = Field(default=None, alias='ADMIN_PASSWORD')
    database_dsn: str = Field(default='sqlite:///./a1_monitor.db', alias='DATABASE_DSN')
    source_import_source: str = Field(default='csv', alias='SOURCE_IMPORT_SOURCE')
    source_csv_path: str | None = Field(default=None, alias='SOURCE_CSV_PATH')
    source_google_sheet_export_url: str | None = Field(default=None, alias='SOURCE_GOOGLE_SHEET_EXPORT_URL')
    scan_pages_limit: int = Field(default=3, alias='SCAN_PAGES_LIMIT', ge=1, le=10)
    scan_interval_minutes: int = Field(default=360, alias='SCAN_INTERVAL_MINUTES', ge=1)
    scan_enabled_engines: str = Field(default='auto_ru,avito', alias='SCAN_ENABLED_ENGINES')
    playwright_headless: bool = Field(default=True, alias='PLAYWRIGHT_HEADLESS')
    request_timeout_seconds: int = Field(default=25, alias='REQUEST_TIMEOUT_SECONDS', ge=5)
    evidence_dir: str = Field(default='./artifacts', alias='EVIDENCE_DIR')
    run_every_minutes: int = Field(default=30, alias='RUN_EVERY_MINUTES', ge=1)
    report_retention_days: int = Field(default=90, alias='REPORT_RETENTION_DAYS', ge=1)
    import_min_valid_ratio: float = Field(default=0.7, alias='IMPORT_MIN_VALID_RATIO', gt=0, le=1)
    app_version: str = '0.2.0'
    min_confirmed_absence_runs: int = 2
    weekend_watch_critical_gap_minutes: int = 24 * 60

    @field_validator('source_import_source')
    @classmethod
    def _validate_source_import_source(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {'csv', 'sheet', 'xlsx', 'excel', 'auto'}:
            raise ValueError('source_import_source must be csv, sheet, xlsx, excel or auto')
        return value

    @field_validator('admin_username', 'admin_password', mode='before')
    @classmethod
    def _empty_credentials_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @field_validator('source_google_sheet_export_url', mode='before')
    @classmethod
    def _empty_to_none_for_sheet_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator('scan_enabled_engines', mode='before')
    @classmethod
    def _validate_scan_engines(cls, value):
        if isinstance(value, str):
            tokens = [token.strip().lower() for token in value.split(',') if token.strip()]
            return ','.join(tokens)
        if isinstance(value, list):
            tokens = [str(x).strip().lower() for x in value if str(x).strip()]
            return ','.join(tokens)
        return 'auto_ru,avito'

    @property
    def scan_engines(self) -> List[str]:
        return [token.strip() for token in self.scan_enabled_engines.split(',') if token.strip()]

    @field_validator('evidence_dir')
    @classmethod
    def _ensure_evidence_dir(cls, value: str) -> str:
        default_path = 'artifacts'
        if not value:
            return default_path

        requested = Path(value).expanduser()
        try:
            requested.mkdir(parents=True, exist_ok=True)
            return str(requested)
        except OSError:
            fallback = Path(default_path)
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)


settings = Settings()
