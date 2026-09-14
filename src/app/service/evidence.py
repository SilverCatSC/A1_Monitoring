from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.models import ListingObservation
from app.scraper.base import EVIDENCE_SCHEMA_VERSION, evidence_manifest_name


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


def read_evidence_manifest(stored: str | None, evidence_dir: str) -> dict[str, Any]:
    """Return only a manifest that belongs to an intact evidence image."""
    image = resolve_named_evidence(stored, evidence_dir)
    manifest = image.with_name(evidence_manifest_name(image.name))
    if not manifest.is_file() or manifest.is_symlink():
        raise EvidenceAccessError('evidence manifest not found')
    try:
        if manifest.stat().st_size > 32_000:
            raise EvidenceAccessError('invalid evidence manifest')
        payload = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceAccessError('invalid evidence manifest') from exc
    if not isinstance(payload, dict):
        raise EvidenceAccessError('invalid evidence manifest')
    required = {
        'schema_version', 'source', 'purpose', 'page_number', 'screenshot_file',
        'screenshot_sha256', 'screenshot_bytes', 'captured_at',
        'requested_url_sha256', 'final_url_sha256',
    }
    if not required <= payload.keys() or payload.get('schema_version') != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceAccessError('invalid evidence manifest')
    if payload.get('screenshot_file') != image.name or payload.get('screenshot_bytes') != image.stat().st_size:
        raise EvidenceAccessError('evidence manifest does not match image')
    if payload.get('screenshot_sha256') != _sha256_file(image):
        raise EvidenceAccessError('evidence image integrity check failed')
    return payload


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
            manifest = path.with_name(evidence_manifest_name(path.name))
            if manifest.is_file() and not manifest.is_symlink():
                manifest.unlink()
            removed += 1
    return removed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
