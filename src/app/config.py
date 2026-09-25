from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SCAN_ALLOWED_NETWORK_PROFILES = frozenset({'local_browser', 'local_no_vpn', 'cloud_no_vpn'})
BUSINESS_TRUSTED_NETWORK_PROFILES = SCAN_ALLOWED_NETWORK_PROFILES
PRODUCTION_NETWORK_PROFILES = frozenset({'local_browser', 'cloud_no_vpn'})


def is_non_loopback_cdp_url(value: str | None) -> bool:
    """Return whether a CDP endpoint can point outside the current process.

    A container cannot use its own loopback address to reach the visible host
    browser. This is deliberately a structural check only; the deployment
    operator must separately attest that the host-side CDP route was verified.
    """
    if not value:
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or '').rstrip('.').lower()
    if parsed.scheme not in {'http', 'https'} or not host or port is None:
        return False
    if host in {'localhost', 'localhost.localdomain'}:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not address.is_loopback and not address.is_unspecified


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_env: str = Field(default='development', alias='APP_ENV')
    app_tz: str = Field(default='Europe/Moscow', alias='APP_TZ')
    network_profile: str = Field(default='unknown', alias='NETWORK_PROFILE')
    auth_enabled: bool = Field(default=False, alias='AUTH_ENABLED')
    admin_username: str | None = Field(default=None, alias='ADMIN_USERNAME')
    admin_password: str | None = Field(default=None, alias='ADMIN_PASSWORD')
    auth_users_json: str | None = Field(default=None, alias='AUTH_USERS_JSON')
    database_dsn: str = Field(default='sqlite:///./a1_monitor.db', alias='DATABASE_DSN')
    source_import_source: str = Field(default='csv', alias='SOURCE_IMPORT_SOURCE')
    source_csv_path: str | None = Field(default=None, alias='SOURCE_CSV_PATH')
    source_google_sheet_export_url: str | None = Field(default=None, alias='SOURCE_GOOGLE_SHEET_EXPORT_URL')
    head_table_google_sheet_export_url: str | None = Field(
        default=(
            'https://docs.google.com/spreadsheets/d/'
            '1s4RSn6oE8CeHzASjGHcQNQK6Hvhje6CUOaFmA3C5UFA/export?format=csv&gid=0'
        ),
        alias='HEAD_TABLE_GOOGLE_SHEET_EXPORT_URL',
    )
    placement_feed_workbook_url: str | None = Field(
        default=None, alias='PLACEMENT_FEED_WORKBOOK_URL'
    )
    company_site_catalog_url: str = Field(
        default='https://a1auto.ru/cars-for-sale/', alias='COMPANY_SITE_CATALOG_URL'
    )
    company_site_audit_dir: str = Field(
        default='./artifacts/company_site_audits', alias='COMPANY_SITE_AUDIT_DIR'
    )
    head_table_audit_dir: str = Field(
        default='./artifacts/head_table_audits', alias='HEAD_TABLE_AUDIT_DIR'
    )
    scan_pages_limit: int = Field(default=3, alias='SCAN_PAGES_LIMIT', ge=1, le=10)
    scan_interval_minutes: int = Field(default=360, alias='SCAN_INTERVAL_MINUTES', ge=1)
    scan_enabled_engines: str = Field(default='auto_ru,avito', alias='SCAN_ENABLED_ENGINES')
    scheduler_enabled: bool = Field(default=False, alias='SCHEDULER_ENABLED')
    host_cdp_scheduler_verified: bool = Field(
        default=False, alias='HOST_CDP_SCHEDULER_VERIFIED'
    )
    playwright_headless: bool = Field(default=True, alias='PLAYWRIGHT_HEADLESS')
    browser_cdp_url: str | None = Field(default=None, alias='BROWSER_CDP_URL')
    local_browser_host_admission: bool = Field(
        default=False, alias='LOCAL_BROWSER_HOST_ADMISSION'
    )
    vpn_admission_path: str = Field(
        default='./artifacts/vpn_admission/attestation.json', alias='VPN_ADMISSION_PATH'
    )
    vpn_operational_policy_path: str = Field(
        default='./artifacts/vpn_admission/policy.json', alias='VPN_OPERATIONAL_POLICY_PATH'
    )
    request_timeout_seconds: int = Field(default=25, alias='REQUEST_TIMEOUT_SECONDS', ge=5)
    auto_ru_page_delay_seconds: float = Field(
        default=2.5, alias='AUTO_RU_PAGE_DELAY_SECONDS', ge=0.5, le=30
    )
    avito_page_delay_seconds: float = Field(
        default=2.5, alias='AVITO_PAGE_DELAY_SECONDS', ge=0.5, le=30
    )
    scan_page_pause_min_seconds: float = Field(
        default=0, alias='SCAN_PAGE_PAUSE_MIN_SECONDS', ge=0, le=120
    )
    scan_page_pause_max_seconds: float = Field(
        default=0, alias='SCAN_PAGE_PAUSE_MAX_SECONDS', ge=0, le=120
    )
    scan_filter_pause_min_seconds: float = Field(
        default=0, alias='SCAN_FILTER_PAUSE_MIN_SECONDS', ge=0, le=180
    )
    scan_filter_pause_max_seconds: float = Field(
        default=0, alias='SCAN_FILTER_PAUSE_MAX_SECONDS', ge=0, le=180
    )
    target_closed_retry_seconds: float = Field(
        default=5, alias='TARGET_CLOSED_RETRY_SECONDS', ge=0, le=30
    )
    captcha_operator_wait_seconds: int = Field(
        default=0, alias='CAPTCHA_OPERATOR_WAIT_SECONDS', ge=0, le=600
    )
    evidence_dir: str = Field(default='./artifacts', alias='EVIDENCE_DIR')
    run_every_minutes: int = Field(default=30, alias='RUN_EVERY_MINUTES', ge=1)
    report_retention_days: int = Field(default=90, alias='REPORT_RETENTION_DAYS', ge=1)
    dealer_discovery_enabled: bool = Field(default=True, alias='DEALER_DISCOVERY_ENABLED')
    dealer_auto_urls: str = Field(default='', alias='DEALER_AUTO_URLS')
    dealer_avito_urls: str = Field(default='', alias='DEALER_AVITO_URLS')
    dealer_pages_limit: int = Field(default=3, alias='DEALER_PAGES_LIMIT', ge=1, le=10)
    seller_preflight_pages: int = Field(default=5, alias='SELLER_PREFLIGHT_PAGES', ge=1, le=10)
    seller_direct_checks_limit: int = Field(default=5, alias='SELLER_DIRECT_CHECKS_LIMIT', ge=0, le=20)
    seller_detail_checks_limit: int = Field(
        default=100, alias='SELLER_DETAIL_CHECKS_LIMIT', ge=0, le=250
    )
    placement_reconciliation_enabled: bool = Field(
        default=False, alias='PLACEMENT_RECONCILIATION_ENABLED'
    )
    seller_identity_checks_limit: int = Field(
        default=80, alias='SELLER_IDENTITY_CHECKS_LIMIT', ge=0, le=100
    )
    import_min_valid_ratio: float = Field(default=0.7, alias='IMPORT_MIN_VALID_RATIO', gt=0, le=1)
    app_version: str = '0.14.0'
    min_confirmed_absence_runs: int = 2
    weekend_watch_critical_gap_minutes: int = 24 * 60

    @field_validator('app_env')
    @classmethod
    def _validate_app_env(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {'development', 'stage', 'production'}:
            raise ValueError('app_env must be development, stage or production')
        return normalized

    @field_validator('source_import_source')
    @classmethod
    def _validate_source_import_source(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {'csv', 'sheet', 'xlsx', 'excel', 'auto'}:
            raise ValueError('source_import_source must be csv, sheet, xlsx, excel or auto')
        return value

    @field_validator('network_profile')
    @classmethod
    def _validate_network_profile(cls, value: str) -> str:
        normalized = value.lower().strip()
        allowed = {'unknown', 'local_vpn', 'local_browser', 'local_no_vpn', 'cloud_no_vpn'}
        if normalized not in allowed:
            raise ValueError(f'network_profile must be one of: {", ".join(sorted(allowed))}')
        return normalized

    @field_validator('admin_username', 'admin_password', 'auth_users_json', mode='before')
    @classmethod
    def _empty_credentials_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @field_validator('browser_cdp_url', mode='before')
    @classmethod
    def _empty_browser_cdp_url_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @field_validator('vpn_admission_path', mode='before')
    @classmethod
    def _validate_vpn_admission_path(cls, value: str | None) -> str:
        clean = str(value or '').strip()
        if not clean:
            raise ValueError('VPN_ADMISSION_PATH must not be empty')
        return clean

    @field_validator('vpn_operational_policy_path', mode='before')
    @classmethod
    def _validate_vpn_operational_policy_path(cls, value: str | None) -> str:
        clean = str(value or '').strip()
        if not clean:
            raise ValueError('VPN_OPERATIONAL_POLICY_PATH must not be empty')
        return clean

    @field_validator('source_google_sheet_export_url', 'head_table_google_sheet_export_url',
                     'placement_feed_workbook_url', mode='before')
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

    @property
    def dealer_sources(self) -> dict[str, list[str]]:
        return {
            'auto_ru': [url.strip() for url in self.dealer_auto_urls.split(',') if url.strip()],
            'avito': [url.strip() for url in self.dealer_avito_urls.split(',') if url.strip()],
        }

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
