"""Immutable, privacy-bounded roster manifests for full monitoring cycles."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import EngineType, Listing, MonitoringCycle, VehicleFilterExpectation
from app.scraper.base import canonical_listing_key


class CycleLedgerError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


class CycleLedgerService:
    """Persist lifecycle state and seal one effective roster before search."""

    def __init__(self, db: Session, evidence_dir: str | None = None):
        self.db = db
        self.evidence_dir = Path(evidence_dir or settings.evidence_dir).resolve()

    def start(self, *, retry_of_cycle_id: str | None = None) -> MonitoringCycle:
        if retry_of_cycle_id:
            self.ensure_retryable(retry_of_cycle_id)
        cycle = MonitoringCycle(
            network_profile=settings.network_profile,
            app_version=settings.app_version,
            started_at=_utcnow(),
            status='preparing',
            retry_of_cycle_id=retry_of_cycle_id,
        )
        self.db.add(cycle)
        # The record must survive an import transaction rollback.
        self.db.commit()
        return cycle

    def ensure_retryable(self, cycle_id: str) -> MonitoringCycle:
        parent = self._cycle(cycle_id)
        if parent.status not in {'partial', 'failed'} or parent.finished_at is None:
            raise CycleLedgerError('only a finished partial or failed cycle can be retried')
        return parent

    def seal_roster(self, cycle_id: str, import_snapshot_id: str | None) -> dict:
        cycle = self._cycle(cycle_id)
        roster = self._roster()
        roster_hash = _sha256_json(roster)
        manifest = {
            'schema_version': 1,
            'cycle_id': cycle.id,
            'created_at': _utcnow().isoformat(),
            'network_profile': cycle.network_profile,
            'app_version': cycle.app_version,
            'source_import_snapshot_id': import_snapshot_id,
            'roster_count': len(roster),
            'roster_sha256': roster_hash,
            'roster': roster,
        }
        relative_path = Path('cycles') / cycle.id / 'manifest.json'
        self._write_once(relative_path, manifest)
        cycle.roster_count = len(roster)
        cycle.roster_sha256 = roster_hash
        cycle.manifest_path = relative_path.as_posix()
        cycle.status = 'running'
        self.db.commit()
        return {
            'id': cycle.id,
            'roster_count': cycle.roster_count,
            'roster_sha256': cycle.roster_sha256,
            'manifest_path': cycle.manifest_path,
            'retry_of_cycle_id': cycle.retry_of_cycle_id,
        }

    def complete(self, cycle_id: str, summary: dict) -> dict:
        cycle = self._cycle(cycle_id)
        cycle.status = str(summary.get('status') or 'partial')
        cycle.summary = summary
        cycle.error = None
        cycle.finished_at = _utcnow()
        self.db.commit()
        return self.describe(cycle)

    def fail(self, cycle_id: str, error: str) -> None:
        cycle = self.db.get(MonitoringCycle, cycle_id)
        if cycle is None:
            return
        cycle.status = 'failed'
        cycle.error = error
        cycle.finished_at = _utcnow()
        self.db.commit()

    def recover_open_cycles(self, *, actor: str) -> list[dict]:
        """Fail only ledger rows left open after the monitoring lock is confirmed idle.

        Callers must hold the complete-cycle lock. This method never changes a
        roster, source snapshot, observation, or completed cycle; it only gives
        an interrupted `preparing`/`running` ledger entry a durable terminal
        state so an operator can see and retry it.
        """
        recovered_by = actor.strip()
        if not recovered_by:
            raise CycleLedgerError('recovery actor is required')
        cycles = (
            self.db.query(MonitoringCycle)
            .filter(
                MonitoringCycle.status.in_({'preparing', 'running'}),
                MonitoringCycle.finished_at.is_(None),
            )
            .order_by(MonitoringCycle.started_at, MonitoringCycle.id)
            .all()
        )
        if not cycles:
            return []

        recovered_at = _utcnow()
        reason = (
            'Recovered after the complete-cycle lock confirmed no active runner; '
            'the previous runner ended before writing a terminal ledger state.'
        )
        for cycle in cycles:
            cycle.status = 'failed'
            cycle.error = reason
            cycle.finished_at = recovered_at
            cycle.summary = {
                'status': 'failed',
                'reason': 'interrupted_runner_recovery',
                'recovered_at': recovered_at.isoformat(),
                'recovered_by': recovered_by,
            }
        self.db.commit()
        return [self.describe(cycle) for cycle in cycles]

    @staticmethod
    def describe(cycle: MonitoringCycle) -> dict:
        return {
            'id': cycle.id,
            'status': cycle.status,
            'roster_count': cycle.roster_count,
            'roster_sha256': cycle.roster_sha256,
            'manifest_path': cycle.manifest_path,
        }

    def _cycle(self, cycle_id: str) -> MonitoringCycle:
        cycle = self.db.get(MonitoringCycle, cycle_id)
        if cycle is None:
            raise CycleLedgerError(f'monitoring cycle not found: {cycle_id}')
        return cycle

    def _roster(self) -> list[dict]:
        listings = (
            self.db.query(Listing)
            .options(
                selectinload(Listing.expectations).selectinload(VehicleFilterExpectation.filter)
            )
            .filter(Listing.is_active.is_(True))
            .order_by(Listing.id)
            .all()
        )
        roster = []
        for listing in listings:
            filters = [
                {
                    'id': expectation.filter_id,
                    'source': expectation.filter.source.value,
                    'external_key': expectation.filter.external_key,
                    'version': expectation.filter.version,
                }
                for expectation in listing.expectations
                if expectation.filter.active
            ]
            filters.sort(key=lambda value: (value['source'], value['id']))
            roster.append(
                {
                    'listing_id': listing.id,
                    # A hash lets an operator prove roster identity without exporting a VIN.
                    'vehicle_signature_sha256': hashlib.sha256(
                        listing.vehicle_signature.encode('utf-8')
                    ).hexdigest(),
                    'sources': {
                        'auto_ru': canonical_listing_key(EngineType.AUTO_RU, listing.source_auto_ru),
                        'avito': canonical_listing_key(EngineType.AVITO, listing.source_avito),
                    },
                    'filters': filters,
                }
            )
        return roster

    def _write_once(self, relative_path: Path, payload: dict) -> None:
        target = self.evidence_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('x', encoding='utf-8') as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as exc:
            raise CycleLedgerError(f'cycle manifest already exists: {relative_path}') from exc
        except OSError as exc:
            raise CycleLedgerError(f'cannot write cycle manifest: {relative_path}') from exc
