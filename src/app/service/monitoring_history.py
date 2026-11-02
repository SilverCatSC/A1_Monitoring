"""Read-only lifecycle from dated card evidence; never infer activity between visits."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from app.models import EngineType
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.service.artifact_bundle import verify_bundle
from app.service.evidence import read_direct_card_evidence, resolve_named_evidence
from app.service.placement_identity import parse_placement_id


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('undated observation')
    return parsed.astimezone(UTC)


def publication_key(source, url):
    try:
        parsed = urlsplit(url or '')
        if (parsed.scheme != 'https' or parsed.hostname not in {'auto.ru', 'www.auto.ru', 'avito.ru', 'www.avito.ru'}
                or parsed.username or parsed.password or parsed.port):
            return None
        key = canonical_listing_key(EngineType(source), url)
        return source + ':' + key if key else None
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=8192)
def _proof(saved_json, root, signature):
    return read_direct_card_evidence(json.loads(saved_json), root)


def load_history(evidence_dir, *, until=None, cycle_ids=None):
    root = Path(evidence_dir).resolve()
    cutoff = timestamp(until) if until else datetime.now(UTC)
    reports, observations, rejected = [], [], 0
    for path in sorted((root / 'cycles').glob('*/placement_reconciliation.json')):
        try:
            if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('invalid report file')
            raw = path.read_bytes()
            report = json.loads(raw)
            cid = str(UUID(report['cycle_id']))
            if cycle_ids is not None and cid not in cycle_ids:
                continue
            date = timestamp(report['created_at_utc'])
            if date > cutoff:
                continue
            if cid != path.parent.name or report.get('schema_version') != 1:
                raise ValueError('wrong cycle')
            if report.get('artifact_bundle') is not None:
                if report['artifact_bundle'] != 'report_bundle.ready.json':
                    raise ValueError('invalid marker')
                verify_bundle(path.parent, 'report_bundle.ready.json', required=(
                    'placement_reconciliation.json', 'analytical_summary.json', 'marketing_summary.txt'))
            cards = report['observed_cards']
            if not isinstance(cards, list) or len(cards) > 1000 or any(not isinstance(c, dict) for c in cards):
                raise ValueError('invalid cards')
            reports.append({'cycle_id': cid, 'date': date.isoformat(),
                            'counts': {s: sum(c.get('source') == s and c.get('state') == 'active'
                                             for c in cards) for s in ('auto_ru', 'avito')},
                            'finding_count': len(report.get('findings', [])),
                            'sha256': hashlib.sha256(raw).hexdigest()})
            for saved in cards:
                if saved.get('state') not in ('active', 'removed'):
                    continue
                try:
                    key = publication_key(saved['source'], saved['url'])
                    if not key:
                        raise ValueError('invalid publication')
                    image = resolve_named_evidence(saved.get('evidence'), str(root))
                    manifest = image.with_name(evidence_manifest_name(image.name))
                    if manifest.is_symlink():
                        raise ValueError('symlink in evidence manifest')
                    signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (image, manifest))
                    proof = _proof(json.dumps(saved, sort_keys=True), str(root), signature)
                    observed = timestamp(proof['captured_at'])
                    if observed > date or observed > cutoff:
                        raise ValueError('future observation')
                    card = saved.get('card') or {}
                    uid = None
                    try:
                        uid = parse_placement_id(card.get('placement_id') or '').value
                    except (ValueError, TypeError):
                        pass
                    observations.append({'key': key, 'source': saved['source'], 'url': saved['url'],
                                         'unique_id': uid, 'at': observed.isoformat(), 'state': saved['state'],
                                         'title': card.get('title') or saved.get('title') or 'Автомобиль',
                                         'price': card.get('price'), 'cycle_id': cid})
                except (OSError, ValueError, KeyError, TypeError):
                    rejected += 1
        except (OSError, ValueError, KeyError, TypeError):
            rejected += 1
    result = summarize_observations(observations)
    result.update(reports=sorted(reports, key=lambda r: (r['date'], r['cycle_id']), reverse=True),
                  excluded_records=rejected, as_of=cutoff.isoformat())
    return result


def summarize_observations(observations):
    """Count UTC dates with active evidence, not 24-hour periods or assumed uptime.

    Reused screenshots retain capture time. A URL can inherit a later ID only
    when every confirmed ID for that exact publication agrees. VIN/price never join.
    """
    ids = defaultdict(set)
    for item in observations:
        if item.get('unique_id'):
            ids[item['key']].add(item['unique_id'])
    groups = defaultdict(list)
    for item in observations:
        known = ids[item['key']]
        uid = next(iter(known)) if len(known) == 1 else None
        group = item['source'] + ':id:' + uid if uid else item['key']
        groups[group].append({**item, 'resolved_id': uid})
    rows, by_url = [], {}
    for group, values in groups.items():
        # Repeated/reused observations do not create additional dates or events.
        states = defaultdict(set)
        for v in values:
            states[(v['key'], timestamp(v['at']))].add(v['state'])
        events = {(v['key'], v['at'], v['state']): v for v in values
                  if len(states[(v['key'], timestamp(v['at']))]) == 1}
        ordered = sorted(events.values(), key=lambda v: (timestamp(v['at']), v['key'], v['state']))
        if not ordered:
            continue
        active_dates = sorted({timestamp(v['at']).date() for v in ordered if v['state'] == 'active'})
        first, last = (active_dates[0], active_dates[-1]) if active_dates else (None, None)
        span = (last - first).days + 1 if first else 0
        row = {'id': hashlib.sha256(group.encode()).hexdigest()[:20], 'source': values[0]['source'],
               'unique_id': values[0]['resolved_id'], 'title': ordered[-1]['title'],
               'active_days': len(active_dates), 'span_days': span,
               'unconfirmed_days': span - len(active_dates),
               'first_active': str(first) if first else None, 'last_active': str(last) if last else None,
               'events': ordered, 'publications': len({v['key'] for v in values})}
        rows.append(row)
        for v in values:
            by_url[v['key']] = row
    return {'rows': sorted(rows, key=lambda r: (r['source'], r['title'], r['id'])), 'by_url': by_url}
