from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import ListingObservation


class EvidenceAccessError(ValueError):
    pass


def resolve_named_evidence(stored: str | None, evidence_dir: str) -> Path:
    if not isinstance(stored, str) or not stored.strip():
        raise EvidenceAccessError('evidence not found')
    root = Path(evidence_dir).resolve()
    requested = Path(stored)
    candidate = requested if requested.is_absolute() else root / requested
    if candidate.suffix.lower() != '.png':
        raise EvidenceAccessError('invalid evidence file')
    current = candidate
    while current != root and current != current.parent:
        if current.is_symlink():
            raise EvidenceAccessError('invalid evidence file')
        current = current.parent
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise EvidenceAccessError('invalid evidence path') from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise EvidenceAccessError('invalid evidence file')
    return resolved


def has_card_evidence(observation: ListingObservation) -> bool:
    return bool((observation.raw_payload or {}).get('card_evidence'))


def evidence_pages(observation: ListingObservation) -> list[int]:
    raw_payload = observation.raw_payload or {}
    if has_card_evidence(observation) and observation.page_number > 0:
        return [observation.page_number]
    diagnostics = raw_payload.get('scan_diagnostics') or {}
    pages = []
    for key, value in diagnostics.items():
        if not key.startswith('page_') or not key.endswith('_evidence') or not value:
            continue
        try:
            pages.append(int(key.removeprefix('page_').removesuffix('_evidence')))
        except ValueError:
            continue
    unique = sorted(set(pages))
    if observation.found and observation.page_number in unique:
        unique.remove(observation.page_number)
        unique.insert(0, observation.page_number)
    return unique


def resolve_observation_evidence(
    observation: ListingObservation,
    page_number: int,
    evidence_dir: str,
) -> Path:
    if page_number < 1:
        raise EvidenceAccessError('page number must be positive')
    raw_payload = observation.raw_payload or {}
    diagnostics = raw_payload.get('scan_diagnostics') or {}
    stored = (
        raw_payload.get('card_evidence')
        if observation.found and observation.page_number == page_number
        else None
    ) or diagnostics.get(f'page_{page_number}_evidence')
    return resolve_named_evidence(stored, evidence_dir)


def cleanup_evidence(evidence_dir: str, retention_days: int) -> int:
    root = Path(evidence_dir).resolve()
    if not root.exists() or not root.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    removed = 0
    for path in root.glob('*.png'):
        if not path.is_file() or path.is_symlink():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed
