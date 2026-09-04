import os
from datetime import UTC, datetime, timedelta

import pytest

from app.models import ListingObservation
from app.service.evidence import (
    EvidenceAccessError,
    cleanup_evidence,
    evidence_pages,
    resolve_observation_evidence,
)


def test_evidence_cleanup_only_removes_expired_png_files(tmp_path):
    old_png = tmp_path / 'old.png'
    fresh_png = tmp_path / 'fresh.png'
    old_text = tmp_path / 'keep.txt'
    for path in (old_png, fresh_png, old_text):
        path.write_text('fixture')
    old_timestamp = (datetime.now(UTC) - timedelta(days=100)).timestamp()
    os.utime(old_png, (old_timestamp, old_timestamp))
    os.utime(old_text, (old_timestamp, old_timestamp))

    assert cleanup_evidence(str(tmp_path), retention_days=90) == 1
    assert not old_png.exists()
    assert fresh_png.exists()
    assert old_text.exists()


def _observation(path: str) -> ListingObservation:
    return ListingObservation(
        raw_payload={
            'scan_diagnostics': {
                'page_1_evidence': path,
                'page_bad_evidence': 'ignored.png',
            }
        }
    )


def test_evidence_resolver_only_returns_png_inside_evidence_root(tmp_path):
    screenshot = tmp_path / 'scan.png'
    screenshot.write_bytes(b'png fixture')
    observation = _observation(str(screenshot))

    assert evidence_pages(observation) == [1]
    assert resolve_observation_evidence(observation, 1, str(tmp_path)) == screenshot

    outside = tmp_path.parent / 'outside.png'
    outside.write_bytes(b'outside')
    observation.raw_payload['scan_diagnostics']['page_1_evidence'] = str(outside)
    with pytest.raises(EvidenceAccessError, match='invalid evidence path'):
        resolve_observation_evidence(observation, 1, str(tmp_path))


def test_evidence_resolver_rejects_missing_files_and_symlinks(tmp_path):
    missing = _observation('missing.png')
    with pytest.raises(EvidenceAccessError, match='invalid evidence path'):
        resolve_observation_evidence(missing, 1, str(tmp_path))

    target = tmp_path / 'target.png'
    target.write_bytes(b'png fixture')
    link = tmp_path / 'link.png'
    link.symlink_to(target)
    with pytest.raises(EvidenceAccessError, match='invalid evidence file'):
        resolve_observation_evidence(_observation('link.png'), 1, str(tmp_path))
