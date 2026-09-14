from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class EngineType(str, enum.Enum):
    AUTO_RU = 'auto_ru'
    AVITO = 'avito'


class SourceStatus(str, enum.Enum):
    ENABLED = 'enabled'
    DISABLED = 'disabled'


class ObservationState(str, enum.Enum):
    FOUND = 'found'
    ABSENT_CONFIRMED = 'absent_confirmed'
    ABSENT_UNCERTAIN = 'absent_uncertain'
    FILTER_MISMATCH = 'filter_mismatch'
    TECHNICAL_ERROR = 'technical_error'


class FeedbackStatus(str, enum.Enum):
    NEW = 'new'
    CHECKING = 'checking'
    ASSIGNED = 'assigned'
    FIXED = 'fixed'
    CONFIRMED = 'confirmed'


class ScanRunStatus(str, enum.Enum):
    IN_PROGRESS = 'in_progress'
    SUCCESS = 'success'
    PARTIAL = 'partial'
    FAILED = 'failed'


class MonitoringCycle(Base):
    """Durable ledger entry for one complete import-to-card-check attempt."""

    __tablename__ = 'monitoring_cycles'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String, default='running', nullable=False, index=True)
    network_profile: Mapped[str] = mapped_column(String, nullable=False)
    app_version: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    roster_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    roster_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceImportSnapshot(Base):
    __tablename__ = 'source_import_snapshots'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey('monitoring_cycles.id'), index=True, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_signature: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    blocked_by_schema_drift: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportFieldDrift(Base):
    __tablename__ = 'import_field_drift'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('source_import_snapshots.id'), index=True)
    raw_headers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    missing_required: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    unknown: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SearchFilter(Base):
    __tablename__ = 'search_filters'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    external_key: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    raw_criteria: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    expectations: Mapped[list['VehicleFilterExpectation']] = relationship(
        back_populates='filter', cascade='all, delete-orphan'
    )
    observations: Mapped[list['ListingObservation']] = relationship(
        'ListingObservation', back_populates='filter'
    )

    __table_args__ = (
        UniqueConstraint(
            'source',
            'external_key',
            'name',
            name='uq_filter_source_key_name',
        ),
    )


class Listing(Base):
    __tablename__ = 'listings'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    vehicle_signature: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    brand: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    generation: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vin: Mapped[str | None] = mapped_column(String, nullable=True)
    source_auto_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_avito: Mapped[str | None] = mapped_column(Text, nullable=True)
    dealer_auto_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    dealer_avito: Mapped[str | None] = mapped_column(Text, nullable=True)
    direct_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_hint: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    expectations: Mapped[list['VehicleFilterExpectation']] = relationship(
        back_populates='listing', cascade='all, delete-orphan'
    )
    observations: Mapped[list['ListingObservation']] = relationship(
        'ListingObservation', back_populates='listing'
    )
    link_events: Mapped[list['ListingLinkEvent']] = relationship(
        'ListingLinkEvent', back_populates='listing', cascade='all, delete-orphan'
    )


class ListingChangeEvent(Base):
    """Registry changes observed from an import, not the marketplace publication time."""

    __tablename__ = 'listing_change_events'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey('source_import_snapshots.id'), nullable=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    changes: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    listing: Mapped[Listing] = relationship('Listing')


class ListingLinkEvent(Base):
    __tablename__ = 'listing_link_events'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    old_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_url: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    listing: Mapped[Listing] = relationship('Listing', back_populates='link_events')


class DealerDiscoveryRun(Base):
    __tablename__ = 'dealer_discovery_runs'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey('monitoring_cycles.id'), index=True, nullable=True
    )
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    dealer_url: Mapped[str] = mapped_column(Text, nullable=False)
    network_profile: Mapped[str] = mapped_column(String, default='unknown', nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidates_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ListingLinkOverride(Base):
    """An explicitly confirmed local link; importing a stale Sheet cannot undo it."""

    __tablename__ = 'listing_link_overrides'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    source: Mapped[EngineType] = mapped_column(Enum(EngineType))
    url: Mapped[str] = mapped_column(Text)
    last_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint('listing_id', 'source', name='uq_listing_link_override'),)


class ListingReconciliation(Base):
    """Seller-catalogue membership, separate from search visibility and sale status."""

    __tablename__ = 'listing_reconciliations'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey('monitoring_cycles.id'), index=True, nullable=True
    )
    batch_id: Mapped[str] = mapped_column(String, index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    source: Mapped[EngineType] = mapped_column(Enum(EngineType))
    state: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    listing: Mapped[Listing] = relationship('Listing')


class DealerListingCandidate(Base):
    __tablename__ = 'dealer_listing_candidates'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    external_key: Mapped[str] = mapped_column(String, nullable=False)
    dealer_url: Mapped[str] = mapped_column(Text, nullable=False)
    listing_url: Mapped[str] = mapped_column(Text, nullable=False)
    network_profile: Mapped[str] = mapped_column(String, default='unknown', nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    price_hint: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint('source', 'external_key', name='uq_dealer_candidate_source_key'),
    )


class VehicleFilterExpectation(Base):
    __tablename__ = 'vehicle_filter_expectations'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filter_id: Mapped[str] = mapped_column(ForeignKey('search_filters.id'), index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    expected_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)
    source_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    filter: Mapped[SearchFilter] = relationship(back_populates='expectations')
    listing: Mapped[Listing] = relationship(back_populates='expectations')

    __table_args__ = (UniqueConstraint('filter_id', 'listing_id', name='uq_filter_listing_expectation'),)


class ScanRun(Base):
    __tablename__ = 'scan_runs'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey('monitoring_cycles.id'), index=True, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    network_profile: Mapped[str] = mapped_column(String, default='unknown', nullable=False)
    status: Mapped[ScanRunStatus] = mapped_column(Enum(ScanRunStatus), default=ScanRunStatus.IN_PROGRESS)
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    technical_errors: Mapped[int] = mapped_column(Integer, default=0)
    filters_total: Mapped[int] = mapped_column(Integer, default=0)
    filters_ok: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    observations: Mapped[list['ListingObservation']] = relationship(
        'ListingObservation', back_populates='scan_run'
    )


class ListingObservation(Base):
    __tablename__ = 'listing_observations'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey('scan_runs.id'), index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    filter_id: Mapped[str] = mapped_column(ForeignKey('search_filters.id'), index=True)
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    position_in_page: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[ObservationState] = mapped_column(
        Enum(ObservationState), default=ObservationState.ABSENT_UNCERTAIN
    )
    listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    price_hint: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_by: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    scan_run: Mapped[ScanRun] = relationship(back_populates='observations')
    filter: Mapped[SearchFilter] = relationship('SearchFilter', back_populates='observations')
    listing: Mapped[Listing] = relationship(back_populates='observations')


class AbsenceEpisode(Base):
    __tablename__ = 'absence_episodes'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str] = mapped_column(ForeignKey('listings.id'), index=True)
    filter_id: Mapped[str] = mapped_column(ForeignKey('search_filters.id'), index=True)
    source: Mapped[EngineType] = mapped_column(Enum(EngineType), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=1)
    observed_count: Mapped[int] = mapped_column(Integer, default=0)
    last_missing_run_id: Mapped[str] = mapped_column(ForeignKey('scan_runs.id'), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    listing: Mapped[Listing] = relationship('Listing')

    __table_args__ = (
        Index(
            'uq_open_absence_active',
            'listing_id',
            'filter_id',
            'source',
            unique=True,
            postgresql_where=text('open IS TRUE'),
            sqlite_where=text('open = 1'),
        ),
    )


class ManagerFeedback(Base):
    __tablename__ = 'manager_feedback'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str | None] = mapped_column(ForeignKey('listings.id'), index=True, nullable=True)
    filter_id: Mapped[str | None] = mapped_column(ForeignKey('search_filters.id'), index=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('scan_runs.id'), index=True, nullable=True)
    observed_id: Mapped[str | None] = mapped_column(
        ForeignKey('listing_observations.id'), index=True, nullable=True
    )
    category: Mapped[str] = mapped_column(String, default='other', nullable=False)
    severity: Mapped[str] = mapped_column(String, default='medium')
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    manager_name: Mapped[str | None] = mapped_column(String, nullable=True)
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[FeedbackStatus] = mapped_column(Enum(FeedbackStatus), default=FeedbackStatus.NEW)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    listing: Mapped[Listing | None] = relationship('Listing')
    events: Mapped[list['FeedbackEvent']] = relationship(
        'FeedbackEvent', back_populates='feedback', cascade='all, delete-orphan'
    )


class FeedbackEvent(Base):
    __tablename__ = 'feedback_events'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    feedback_id: Mapped[str] = mapped_column(ForeignKey('manager_feedback.id'), index=True)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    feedback: Mapped[ManagerFeedback] = relationship('ManagerFeedback', back_populates='events')
