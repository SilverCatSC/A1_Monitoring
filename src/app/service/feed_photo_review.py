"""Photo-only follow-up bound to a saved report and its original feed gallery.

Original cycle files, advert observations, identifiers and analytical findings
are immutable. No source/network access happens when displaying this overlay.
"""

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.service.artifact_bundle import publish_bundle, verify_bundle
from app.service.feed_photos import MAX_PREFIX, check_photo_prefix, safe_feed_photo_url

NAME = 'feed_photo_review.json'
MARKER = 'feed_photo_review.ready.json'


def _directory(root, cycle_id):
    if not isinstance(cycle_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', cycle_id):
        raise ValueError('invalid cycle')
    path = Path(root) / 'cycles' / cycle_id
    if path.is_symlink():
        raise ValueError('invalid review directory')
    return path


def _validated_entries(report, root, payload, original_raw):
    if (not isinstance(payload, dict) or payload.get('schema_version') != 1
            or payload.get('cycle_id') != report['cycle_id']
            or payload.get('original_report_sha256') != hashlib.sha256(original_raw).hexdigest()
            or not isinstance(payload.get('entries'), list) or not 1 <= len(payload['entries']) <= 100):
        raise ValueError('invalid photo review scope')
    original = json.loads(original_raw)
    if original.get('cycle_id') != report['cycle_id']:
        raise ValueError('original photo review cycle differs')
    content = original.get('automatic_content_checks') or {}
    originals = {(c['source'], c['url'], c['unique_id']): c for c in content.get('feed_photo_candidates', [])}
    failed = {(c['source'], c['url'], c['unique_id']) for c in content.get('feed_photo_checks', [])
              if c['status'] != 'verified'}
    seen, entries = set(), []
    for entry in payload['entries']:
        key = (entry['source'], entry['url'], entry['unique_id'])
        if key not in failed or key not in originals or key in seen:
            raise ValueError('photo is not one original failed candidate')
        seen.add(key)
        original = originals[key]
        image = entry['image_url']
        if not safe_feed_photo_url(image) or image not in original['gallery_urls']:
            raise ValueError('photo outside saved feed gallery')
        check = entry['verification']
        digest = check['prefix_sha256']
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('invalid photo proof hash')
        stamp = datetime.fromisoformat(check['checked_at_utc'])
        if stamp.tzinfo is None or stamp > datetime.now(UTC):
            raise ValueError('invalid photo check date')
        path = Path(root) / 'feed_photo_samples' / (digest + '.bin')
        if path.parent.is_symlink() or path.is_symlink() or path.stat().st_size > MAX_PREFIX:
            raise ValueError('invalid photo sample')
        verified = check_photo_prefix(path.read_bytes(), check.get('reported_mime', check['mime']))
        if not verified or verified != {k: v for k, v in check.items() if k != 'checked_at_utc'}:
            raise ValueError('photo proof changed or invalid')
        # Only original provenance plus validated media fields can be displayed.
        entries.append({**original, 'image_url': image, 'verification': check, 'status': 'verified',
                        'captured_at_utc': original['feed_read_at_utc']})
    return entries


def save_feed_photo_review(report_path, evidence_dir, entries):
    path = Path(report_path)
    original_raw = path.read_bytes()
    report = json.loads(original_raw)
    directory = _directory(evidence_dir, report['cycle_id'])
    if path.resolve() != (directory / 'placement_reconciliation.json').resolve():
        raise ValueError('photo review requires the original cycle report')
    payload = {'schema_version': 1, 'cycle_id': report['cycle_id'],
               'original_report_sha256': hashlib.sha256(original_raw).hexdigest(),
               'reviewed_at_utc': datetime.now(UTC).isoformat(),
               'basis': 'Image responses rechecked only at saved feed-gallery URLs. Valid raster signature overrides an incorrect raster MIME label; original advert observations unchanged.',
               'entries': entries}
    _validated_entries(report, evidence_dir, payload, original_raw)
    publish_bundle(directory, {NAME: json.dumps(payload, ensure_ascii=False, indent=2) + '\n'},
                   marker=MARKER, commit_member=NAME)
    return payload


def apply_feed_photo_review(report, evidence_dir, *, original_raw):
    directory = _directory(evidence_dir, report['cycle_id'])
    if not (directory / MARKER).exists() and not (directory / NAME).exists():
        return False
    verify_bundle(directory, MARKER, required=(NAME,))
    path = directory / NAME
    if path.stat().st_size > 512 * 1024:
        raise ValueError('photo review too large')
    payload = json.loads(path.read_text())
    entries = _validated_entries(report, evidence_dir, payload, original_raw)
    fixed = {(p['source'], p['url'], p['unique_id']): p for p in entries}
    for content in [report.get('automatic_content_checks'), report.get('content_review')]:
        if not isinstance(content, dict):
            continue
        content['feed_photos'] = [p for p in content.get('feed_photos', [])
                                 if (p['source'], p['url'], p['unique_id']) not in fixed] + entries
        content['feed_photo_checks'] = [
            {**c, 'status': 'verified', 'post_run_media_review': True} if
            (c['source'], c['url'], c['unique_id']) in fixed else c
            for c in content.get('feed_photo_checks', [])]
    report['feed_photo_review_info'] = {
        'reviewed_at_utc': payload['reviewed_at_utc'], 'repaired_publications': len(entries),
        'mime_label_warnings': sum(bool(p['verification'].get('mime_mismatch')) for p in entries),
        'counts': dict(Counter(c['status'] for c in report['automatic_content_checks']['feed_photo_checks'])),
        'original_observations_unchanged': True}
    return True
