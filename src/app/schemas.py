from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import EngineType


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    app_version: str


class TriggerScanResponse(BaseModel):
    status: str
    started_at: datetime
    summary: dict[str, int]


class ImportResponse(BaseModel):
    rows_total: int
    rows_valid: int
    rows_invalid: int


class FilterUpsert(BaseModel):
    source: EngineType
    name: str
    url: str
    active: bool = True
    vins: list[str] = Field(default_factory=list)
    apply_to_all_active: bool = False


class FilterStateChange(BaseModel):
    active: bool


class FeedbackCreate(BaseModel):
    listing_id: str | None = None
    filter_id: str | None = None
    severity: str = 'medium'
    message: str
    manager_name: str | None = None
    source: str | None = None


class KPIResponse(BaseModel):
    observed_found: int
    observed_missed: int
    scan_runs_success: int
    absent_active: int


class MissingListResponse(BaseModel):
    listing_id: str
    filter_id: str
    source: str
    consecutive_misses: int
    started_at: datetime
    opened: bool
