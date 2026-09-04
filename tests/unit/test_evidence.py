import os
from datetime import UTC, datetime, timedelta

from app.service.evidence import cleanup_evidence


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
