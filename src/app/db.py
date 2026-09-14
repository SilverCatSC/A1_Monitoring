from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

engine = create_engine(
    settings.database_dsn,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_compatibility_migrations()


def _apply_compatibility_migrations() -> None:
    """Keep early installations safe until versioned Alembic migrations are introduced."""
    if engine.dialect.name != 'postgresql':
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'ALTER TABLE absence_episodes DROP CONSTRAINT IF EXISTS uq_open_absence'
        )
        connection.exec_driver_sql(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_open_absence_active '
            'ON absence_episodes (listing_id, filter_id, source) WHERE open IS TRUE'
        )
        connection.exec_driver_sql(
            "ALTER TABLE manager_feedback ADD COLUMN IF NOT EXISTS category VARCHAR NOT NULL DEFAULT 'other'"
        )
        connection.exec_driver_sql(
            'ALTER TABLE manager_feedback ADD COLUMN IF NOT EXISTS assignee VARCHAR'
        )
        connection.exec_driver_sql(
            'ALTER TABLE manager_feedback ADD COLUMN IF NOT EXISTS reconciliation_id VARCHAR'
        )
        connection.exec_driver_sql(
            'ALTER TABLE manager_feedback ADD COLUMN IF NOT EXISTS finding_code VARCHAR'
        )
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_manager_feedback_reconciliation_id '
            'ON manager_feedback (reconciliation_id)'
        )
        connection.exec_driver_sql(
            'ALTER TABLE monitoring_cycles ADD COLUMN IF NOT EXISTS retry_of_cycle_id VARCHAR'
        )
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_monitoring_cycles_retry_of_cycle_id '
            'ON monitoring_cycles (retry_of_cycle_id)'
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    db_manager = get_db()
    db = next(db_manager)
    try:
        yield db
    finally:
        try:
            next(db_manager)
        except StopIteration:
            pass
