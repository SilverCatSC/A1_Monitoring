"""Dated publication changes. Vehicle sales and VIN/price identity are never inferred."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from app.models import EngineType
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.service.coverage import absence_proven as coverage_absence_proven
from app.service.placement_identity import PlacementIdError, parse_placement_id


def _uid(card, *, exact=False):
    value = (card.get('card') or {}).get('placement_id')
    if not value and not exact:
        value = card.get('unique_id')
    try:
        return parse_placement_id(value).value if isinstance(value, str) else None
    except PlacementIdError:
        return None


def _proven(card):
    evidence = card.get('evidence')
    return isinstance(evidence, str) and card.get('evidence_manifest') == evidence_manifest_name(evidence)


def _cards(report, source):
    grouped = defaultdict(list)
    for card in report.get('observed_cards', []):
        if (card.get('source') == source and _proven(card)
                and (key := canonical_listing_key(EngineType(source), card.get('url')))):
            grouped[key].append(card)
    # Order must not choose between active/removed observations or different
    # IDs for the same URL. Leave that URL uncertain until explicitly reviewed.
    return {key: cards[-1] for key, cards in grouped.items()
            if len({(c.get('state'), _uid(c, exact=True)) for c in cards}) == 1}


def _segment(source, card):
    path = urlsplit(card['url']).path
    if source == 'avito':
        return 'cars'
    if path.startswith('/lcv/'):
        return 'commercial'
    return 'cars'


def publication_alerts(report, previous_by_source=None):
    """Positive ID matches work with partial coverage; absences retain uncertainty.

    previous_by_source contains only validated earlier cycle reports. Exact
    publication IDs (not aliases, VINs or prices) identify new URL candidates.
    A missing old URL is never automatically written back into marketing.
    """
    alerts = []
    baselines = {}
    selected = set(report.get('catalogue_enumeration_complete', {}))
    if 'catalogue_enumeration_complete' not in report:
        selected.update(c.get('source') for c in report.get('observed_cards', []))

    def emit(source, kind, title, *, old=None, new=None, uid=None, certainty='confirmed', **extra):
        event = {'source': source, 'kind': kind, 'title': title, 'certainty': certainty,
                 'unique_id': uid, 'old_url': old, 'new_url': new,
                 'cause': 'unknown', 'vehicle_sale_confirmed': False,
                 'first_detected_cycle_id': report['cycle_id'], 'ongoing': False, **extra}
        identity = '|'.join(str(event.get(k) or '') for k in ('source','kind','segment','unique_id','old_url','new_url'))
        event['event_id'] = hashlib.sha256(identity.encode()).hexdigest()[:24]
        alerts.append(event)

    for source in sorted(selected & {'auto_ru', 'avito'}):
        current = _cards(report, source)
        uncertain_current = {
            key for c in report.get('observed_cards', []) if c.get('source') == source
            and (key := canonical_listing_key(EngineType(source), c.get('url')))
            and (key not in current or not _proven(c) or c.get('state') not in {'active', 'removed'})
        }
        active = {key: c for key, c in current.items() if c.get('state') == 'active'}
        by_id = defaultdict(dict)
        for key, card in active.items():
            if uid := _uid(card, exact=True):
                by_id[uid][key] = card
        complete = coverage_absence_proven(report, source)
        previous = (previous_by_source or {}).get(source)
        old_cards = _cards(previous or {}, source)
        old_active = {key: c for key, c in old_cards.items() if c.get('state') == 'active'}
        baselines[source] = previous.get('cycle_id') if previous else None
        for uid, candidates in by_id.items():
            if len(candidates) > 1:
                emit(source, 'duplicate_unique_id', 'Один unique_id указан в нескольких активных публикациях',
                     uid=uid, urls=sorted(c['url'] for c in candidates.values()))
        counts = Counter(_segment(source,c) for c in active.values())
        prior_counts = Counter(_segment(source,c) for c in old_active.values())
        for segment, before in prior_counts.items():
            after = counts[segment]
            if after < before:
                emit(source, 'inventory_drop', 'Сократилось число наблюдаемых активных публикаций',
                     certainty='confirmed' if complete and not uncertain_current else 'coverage_unproven', segment=segment,
                     previous_count=before, observed_count=after, catalogue_complete=complete,
                     reference_cycle_id=previous.get('cycle_id'),
                     explanation='Причина неизвестна: снятие, перевыкладка или проблема площадки/выгрузки. При неполном каталоге часть публикаций могла не попасть в проверку.')
        for key, old in old_active.items():
            if key in active:
                continue
            uid = _uid(old)
            candidates = by_id.get(uid, {}) if uid else {}
            old_removed = current.get(key, {}).get('state') == 'removed'
            absence_proven = coverage_absence_proven(report, source, old['url']) and key not in uncertain_current
            if len(candidates) == 1:
                new = next(iter(candidates.values()))
                emit(source, 'republished' if absence_proven or old_removed else 'republication_candidate',
                     'Найдена новая ссылка с тем же unique_id', old=old['url'], new=new['url'], uid=uid,
                     certainty='confirmed' if absence_proven or old_removed else 'old_state_unproven',
                     id_match_basis='exact', evidence=new['evidence'], old_removed=old_removed)
            elif len(candidates) > 1:
                # The duplicate-ID alert already explains why no URL is selected.
                continue
            elif old_removed or absence_proven:
                emit(source, 'publication_disappeared', 'Публикация больше не активна', old=old['url'], uid=uid,
                     evidence=current.get(key, {}).get('evidence'),
                     basis='explicit_removal' if old_removed else 'absent_from_complete_seller_catalogue')
            else:
                emit(source, 'publication_unverified', 'Ранее активная публикация не подтверждена',
                     old=old['url'], uid=uid, certainty='coverage_unproven',
                     explanation='Полнота каталога или состояние прямой карточки не подтверждены; исчезновение не доказано.')
        for key, card in current.items():
            if card.get('state') == 'removed' and key not in old_active:
                emit(source, 'publication_inactive', 'По проверенной ссылке публикация неактивна',
                     old=card['url'], uid=_uid(card), evidence=card['evidence'], basis='explicit_removal')
        for key, card in active.items():
            old = old_cards.get(key)
            if old and old.get('state') == 'removed':
                emit(source, 'publication_restored', 'Публикация снова активна', new=card['url'],
                     uid=_uid(card, exact=True), evidence=card['evidence'], basis='previous_explicit_removal')
        # An unchanged shrunken catalogue is not recovery. Carry open signals
        # explicitly instead of inventing old active cards in this cycle.
        prior_alerts = (previous or {}).get('publication_alerts', {})
        for prior in prior_alerts.get('items', []) if isinstance(prior_alerts, dict) else []:
            if not isinstance(prior, dict) or prior.get('source') != source:
                continue
            first = prior.get('first_detected_cycle_id') or previous.get('cycle_id')
            if prior.get('kind') == 'inventory_drop':
                segment = prior.get('segment')
                before = prior.get('previous_count')
                if segment not in {'cars', 'commercial'} or not isinstance(before, int):
                    continue
                after = counts[segment]
                existing = next((a for a in alerts if a['source'] == source
                                 and a['kind'] == 'inventory_drop' and a.get('segment') == segment), None)
                if before > after and existing is None:
                    emit(source, 'inventory_drop', 'Сокращение числа наблюдаемых публикаций сохраняется',
                         certainty='confirmed' if complete and not uncertain_current else 'coverage_unproven',
                         segment=segment, previous_count=before, observed_count=after,
                         catalogue_complete=complete, reference_cycle_id=prior.get('reference_cycle_id'),
                         first_detected_cycle_id=first, ongoing=True)
                elif existing:
                    existing.update(first_detected_cycle_id=first, ongoing=True)
                    if before > existing['previous_count']:
                        existing.update(previous_count=before, reference_cycle_id=prior.get('reference_cycle_id'))
                continue
            if prior.get('kind') not in {'publication_disappeared', 'publication_inactive', 'publication_unverified', 'republication_candidate'}:
                continue
            old_url = prior.get('old_url')
            key = canonical_listing_key(EngineType(source), old_url)
            if not key:
                continue
            uid = _uid({'unique_id': prior.get('unique_id')})
            existing = next((a for a in alerts if a['source'] == source and a.get('old_url')
                             and canonical_listing_key(EngineType(source), a['old_url']) == key), None)
            if existing:
                existing.update(first_detected_cycle_id=first, ongoing=True)
                continue
            if key in active:
                if not any(a['source'] == source and a['kind'] == 'publication_restored'
                           and a.get('new_url') == active[key]['url'] for a in alerts):
                    was_absent = (prior.get('kind') in {'publication_disappeared', 'publication_inactive'}
                                  and prior.get('certainty') == 'confirmed'
                                  and (prior.get('basis') == 'explicit_removal'
                                       or coverage_absence_proven(previous, source, old_url)))
                    emit(source, 'publication_restored' if was_absent else 'publication_verified',
                         'Публикация снова активна' if was_absent else 'Активность публикации подтверждена',
                         new=active[key]['url'], uid=_uid(active[key], exact=True),
                         evidence=active[key]['evidence'],
                         basis='previous_confirmed_absence' if was_absent else 'previous_state_unverified')
                continue
            candidates = by_id.get(uid, {}) if uid else {}
            if len(candidates) == 1:
                new = next(iter(candidates.values()))
                confirmed = (coverage_absence_proven(report, source, old_url) and key not in uncertain_current) or current.get(key, {}).get('state') == 'removed'
                emit(source, 'republished' if confirmed else 'republication_candidate',
                     'Найдена новая ссылка с тем же unique_id', old=old_url, new=new['url'], uid=uid,
                     certainty='confirmed' if confirmed else 'old_state_unproven',
                     id_match_basis='exact', evidence=new['evidence'], first_detected_cycle_id=first)
                continue
            confirmed = coverage_absence_proven(report, source, old_url) and key not in uncertain_current
            emit(source, 'publication_disappeared' if confirmed else 'publication_unverified',
                 'Ранее исчезнувшая публикация пока не восстановлена', old=old_url, uid=uid,
                 certainty='confirmed' if confirmed else 'coverage_unproven',
                 basis='absent_from_complete_seller_catalogue' if confirmed else 'open_previous_signal',
                 first_detected_cycle_id=first, ongoing=True,
                 previous_signal_kind=prior.get('kind'))
    return {'schema_version': 1, 'cycle_id': report['cycle_id'],
            'baseline_cycle_by_source': baselines, 'total':len(alerts),
            'counts':dict(Counter(a['kind'] for a in alerts)), 'items': alerts,
            'delivery':'cycle_report_and_progress', 'source_writes':False}


def apply_absence_caveats(report):
    """Downgrade stale saved conclusions in memory; never rewrite source evidence."""
    alerts = report.get('publication_alerts')
    if not isinstance(alerts, dict):
        return
    changes = []
    for item in alerts.get('items', []):
        if item.get('kind') == 'publication_restored' and item.get('basis') not in {
            'previous_explicit_removal', 'previous_confirmed_absence',
        }:
            # Archived alerts lack evidence of a prior removal. Current activity
            # is proven, but a blocked prior visit cannot establish restoration.
            item['coverage_reassessment'] = {'original_kind': 'publication_restored',
                                              'original_certainty': item.get('certainty')}
            item['kind'] = 'publication_verified'
            item['title'] = 'Активность публикации подтверждена'
            item['explanation'] = 'Предыдущее отсутствие не доказано; восстановление публикации не установлено.'
            changes.append(item.get('event_id'))
        source = item['source']
        old = item.get('old_url')
        if item.get('certainty') != 'confirmed' or coverage_absence_proven(report, source, old):
            continue
        current = _cards(report, source)
        key = canonical_listing_key(EngineType(source), old) if old else None
        if current.get(key, {}).get('state') == 'removed':
            continue
        kind = item.get('kind')
        if kind not in {'inventory_drop', 'publication_disappeared', 'publication_inactive', 'republished'}:
            continue
        item['coverage_reassessment'] = {'original_kind': kind, 'original_certainty': 'confirmed'}
        item['certainty'] = 'old_state_unproven' if kind == 'republished' else 'coverage_unproven'
        if kind == 'republished':
            item['kind'] = 'republication_candidate'
        elif kind != 'inventory_drop':
            item['kind'] = 'publication_unverified'
            item['title'] = 'Состояние ранее активной публикации требует подтверждения'
        item['explanation'] = 'Сохранённый вывод уточнён: полнота проверки карточек не подтверждена; отсутствие не доказано.'
        changes.append(item.get('event_id'))
    alerts['counts'] = dict(Counter(a['kind'] for a in alerts.get('items', [])))
    if changes:
        report['coverage_reassessment'] = {'changed_alerts': len(changes), 'source_evidence_unchanged': True}


def add_publication_alerts(db, report, evidence_dir):
    """Use the newest earlier valid report separately for each checked source."""
    from app.models import MonitoringCycle
    from app.service.placement_report import PlacementReportError, read_cycle_placement_report
    cycle = db.get(MonitoringCycle, report['cycle_id'])
    baselines = {}
    sources = set(report.get('catalogue_enumeration_complete', {}))
    if cycle is not None:
        previous = (db.query(MonitoringCycle).filter(
            MonitoringCycle.started_at < cycle.started_at,
            MonitoringCycle.finished_at.isnot(None),
            MonitoringCycle.id != cycle.id).order_by(MonitoringCycle.started_at.desc()))
        for candidate in previous.yield_per(20):
            try:
                saved = read_cycle_placement_report(candidate, evidence_dir)
            except PlacementReportError:
                continue
            for source in sources - baselines.keys():
                saved_alerts = saved.get('publication_alerts', {}).get('items', [])
                if (_cards(saved, source) or any(a.get('source') == source for a in saved_alerts)
                        or saved.get('catalogue_enumeration_complete', {}).get(source) is True):
                    baselines[source] = saved
            if sources <= baselines.keys():
                break
    report['publication_alerts'] = publication_alerts(report, baselines)
    return report['publication_alerts']
