"""Read-only dashboard projections. Counts never imply impressions, clicks or publication dates."""
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy.orm import selectinload

from app.config import BUSINESS_TRUSTED_NETWORK_PROFILES
from app.models import (
    EngineType,
    FeedbackEvent,
    FeedbackStatus,
    Listing,
    ListingChangeEvent,
    ListingLinkEvent,
    ListingObservation,
    ManagerFeedback,
    ScanRun,
    SearchFilter,
    SourceImportSnapshot,
)
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url

SOURCES = {'auto_ru': 'Auto.ru', 'avito': 'Avito'}
STATES = {
    'found': 'Найдено', 'absent_confirmed': 'Повторный непоказ',
    'absent_uncertain': 'Не найдено · нужна проверка', 'technical_error': 'Проверка не удалась',
    'filter_mismatch': 'Условия не совпали', 'not_checked': 'Ещё не проверено',
    'not_configured': 'Нет фильтра',
}
VALID = {'found', 'absent_confirmed', 'absent_uncertain'}


def utc(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def local_time(value):
    return utc(value).astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M') if value else '—'


def money(value):
    return f'{value:,.0f}'.replace(',', ' ') + ' ₽' if value is not None else 'Цена не указана'


def summarize(rows):
    counts = Counter(row.state.value for row in rows)
    valid = sum(counts[state] for state in VALID)
    return {
        'total': len(rows), 'found': counts['found'], 'valid': valid,
        'missed': counts['absent_confirmed'] + counts['absent_uncertain'],
        'technical': counts['technical_error'], 'mismatch': counts['filter_mismatch'],
        'rate': round(100 * counts['found'] / valid, 1) if valid else None,
        'pages': [sum(row.state.value == 'found' and row.page_number == p for row in rows) for p in (1, 2, 3)],
    }


def analytics_context(db, *, days=7, source='', brand='', q='', status='', scope='active'):
    days = min(max(days, 1), 90)
    now = datetime.now(UTC)
    tz = ZoneInfo('Europe/Moscow')
    since = (now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(days=days - 1)).astimezone(UTC)
    listings = db.query(Listing).options(
        selectinload(Listing.expectations), selectinload(Listing.link_events),
    ).order_by(Listing.brand, Listing.model, Listing.vin).all()
    brands = sorted({item.brand for item in listings if item.brand})
    selected = [item for item in listings if (not brand or item.brand == brand)
                and (scope == 'all' or item.is_active)
                and (not q or q.casefold() in f'{item.brand} {item.model} {item.vin}'.casefold())]
    ids = {item.id for item in selected}
    filters = {item.id: item for item in db.query(SearchFilter).all()}
    observations = db.query(ListingObservation).join(ScanRun).filter(
        ListingObservation.observed_at >= since,
        ScanRun.network_profile.in_(tuple(BUSINESS_TRUSTED_NETWORK_PROFILES)),
    )
    if source:
        observations = observations.filter(ListingObservation.source == EngineType(source))
    rows = [row for row in observations.order_by(ListingObservation.observed_at.desc()).all()
            if row.listing_id in ids]
    by_listing = {}
    for row in rows:
        by_listing.setdefault((row.listing_id, row.source.value), []).append(row)
    placements = []
    for item in selected:
        for engine in EngineType:
            if source and engine.value != source:
                continue
            url = item.source_auto_ru if engine == EngineType.AUTO_RU else item.source_avito
            if not is_marketplace_listing_url(engine, url):
                continue
            key = canonical_listing_key(engine, url)
            expected = [filters[e.filter_id] for e in item.expectations
                        if e.filter_id in filters and filters[e.filter_id].active
                        and filters[e.filter_id].source == engine]
            expected_ids = {f.id for f in expected}
            replaced_at = max((utc(e.created_at) for e in item.link_events if e.source == engine
                               and canonical_listing_key(engine, e.old_url) != canonical_listing_key(engine, e.new_url)), default=None)
            current_rows = []
            unattributed_latest = {}
            for row in by_listing.get((item.id, engine.value), []):
                payload = row.raw_payload or {}
                observed_key = payload.get('expected_listing_key') or canonical_listing_key(engine, row.listing_url)
                current_filter = filters.get(row.filter_id)
                if (not observed_key and row.filter_id in expected_ids
                        and (not replaced_at or utc(row.observed_at) >= replaced_at)):
                    unattributed_latest.setdefault(row.filter_id, row)
                if observed_key != key or (replaced_at and utc(row.observed_at) < replaced_at):
                    continue
                if not current_filter or row.filter_id not in expected_ids:
                    continue
                if payload.get('filter_version', current_filter.version) != current_filter.version:
                    continue
                if ('filter_version' not in payload and current_filter.version > 1
                        and utc(row.observed_at) < utc(current_filter.updated_at)):
                    continue
                current_rows.append(row)
            latest = {}
            for row in current_rows:
                latest.setdefault(row.filter_id, row)
            # A later legacy result with no identity cannot prove a current miss,
            # but must not allow an older found result to masquerade as the latest check.
            for filter_id, unknown in unattributed_latest.items():
                if filter_id in latest and utc(unknown.observed_at) > utc(latest[filter_id].observed_at):
                    latest.pop(filter_id)
            filter_results = []
            for filt in expected:
                obs = latest.get(filt.id)
                state = obs.state.value if obs else 'not_checked'
                filter_results.append({'name': filt.name, 'state': state, 'label': STATES[state],
                                       'page': obs.page_number if obs and obs.found else None,
                                       'time': local_time(obs.observed_at) if obs else '—'})
            states = [result['state'] for result in filter_results]
            state = next((s for s in ('found', 'technical_error', 'not_checked', 'filter_mismatch',
                                     'absent_uncertain', 'absent_confirmed') if s in states), 'not_configured')
            found = [latest[filt.id] for filt in expected if filt.id in latest and latest[filt.id].found]
            page = min((obs.page_number for obs in found), default=None)
            last = current_rows[0] if current_rows else None
            proof = next((obs for obs in found if (obs.raw_payload or {}).get('card_evidence')), None)
            placements.append({
                'listing_id': item.id, 'name': f'{item.brand or ""} {item.model or ""}'.strip() or 'Автомобиль',
                'vin': item.vin or 'VIN не указан', 'year': item.year, 'active': item.is_active,
                'price': money(item.price_hint), 'source': engine.value, 'source_name': SOURCES[engine.value],
                'url': url, 'key': key, 'state': state, 'label': STATES[state], 'page': page,
                'checked': local_time(last.observed_at) if last else '—', 'filters': filter_results,
                'summary': summarize(current_rows),
                'proof': f'/api/v1/observations/{proof.id}/evidence/{proof.page_number}' if proof else None,
            })
    totals = Counter(item['state'] for item in placements)
    all_placements_count = len(placements)
    if status:
        placements = [item for item in placements if item['state'] == status]
    daily = []
    for offset in range(days - 1, -1, -1):
        day = now.astimezone(tz).date() - timedelta(days=offset)
        day_rows = [row for row in rows if utc(row.observed_at).astimezone(tz).date() == day]
        daily.append({'date': day.isoformat(), 'label': day.strftime('%d.%m'), 'weekend': day.weekday() >= 5,
                      **summarize(day_rows)})
    runs = db.query(ScanRun).filter(ScanRun.started_at >= since).order_by(ScanRun.started_at.desc()).all()
    run_rows = [{
        'id': run.id, 'source': SOURCES[run.source.value], 'time': local_time(run.started_at),
        'status': {'success': 'Завершена', 'partial': 'Частично', 'failed': 'Сбой', 'in_progress': 'Выполняется'}[run.status.value],
        'ok': run.filters_ok, 'total': run.filters_total, 'errors': run.technical_errors,
        'trusted': run.network_profile in BUSINESS_TRUSTED_NETWORK_PROFILES,
        'seconds': round((utc(run.finished_at) - utc(run.started_at)).total_seconds()) if run.finished_at else None,
    } for run in runs if not source or run.source.value == source]
    last_import = db.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
    return {
        'days': days, 'source': source, 'brand': brand, 'q': q, 'status': status, 'scope': scope,
        'query': urlencode({'days': days, 'source': source, 'brand': brand, 'q': q, 'scope': scope}),
        'brands': brands, 'states': STATES, 'generated_at': local_time(now), 'placements': placements,
        'totals': totals, 'placement_count': all_placements_count, 'vehicle_count': len(selected),
        'active_count': sum(item.is_active for item in listings), 'archive_count': sum(not item.is_active for item in listings),
        'missing_links': sum(not item.source_auto_ru or not item.source_avito for item in selected),
        'period': summarize(rows), 'daily': daily,
        'sources': [{'source': value, 'name': name, **summarize([row for row in rows if row.source.value == value])}
                    for value, name in SOURCES.items() if not source or value == source],
        'runs': run_rows[:50], 'runs_count': len(run_rows),
        'open_feedback': db.query(ManagerFeedback).filter(ManagerFeedback.status != FeedbackStatus.CONFIRMED).count(),
        'last_import': {'time': local_time(last_import.finished_at), 'valid': last_import.valid_rows,
                        'total': last_import.raw_rows, 'blocked': last_import.blocked_by_schema_drift} if last_import else None,
        'has_filters': any(f.active for f in filters.values()),
        'legacy_unattributed': sum(not (row.raw_payload or {}).get('expected_listing_key') and not row.listing_url for row in rows),
    }


def activity_context(db, *, days=30, kind='', page=1):
    days = min(max(days, 1), 90)
    since = datetime.now(UTC) - timedelta(days=days)
    events = []
    def append(at, category, title, detail, url=None):
        events.append({'at': utc(at), 'time': local_time(at), 'kind': category, 'title': title, 'detail': detail, 'url': url})
    if kind in ('', 'registry'):
        for event in db.query(ListingChangeEvent).options(selectinload(ListingChangeEvent.listing)).filter(ListingChangeEvent.created_at >= since):
            labels = {'brand': 'Марка', 'model': 'Модель', 'year': 'Год', 'price_hint': 'Цена', 'is_active': 'Активность',
                      'source_auto_ru': 'Ссылка Auto.ru', 'source_avito': 'Ссылка Avito'}
            def display(field, value):
                if value is None:
                    return '—'
                if field == 'price_hint':
                    return money(value)
                if field == 'is_active':
                    return 'Активен' if value else 'Архив'
                return str(value)
            detail = '; '.join(f'{labels.get(field, field)}: {display(field, change["old"])} → {display(field, change["new"])}'
                               for field, change in event.changes.items())
            append(event.created_at, 'registry', f'{event.listing.brand or ""} {event.listing.model or ""} · {event.listing.vin or ""}', detail,
                   f'/api/v1/dashboard/listings/{event.listing_id}')
        for event in db.query(ListingLinkEvent).filter(ListingLinkEvent.created_at >= since, ListingLinkEvent.actor != 'Импорт таблицы'):
            append(event.created_at, 'registry', f'Обновлена ссылка · {event.actor}', event.reason,
                   f'/api/v1/dashboard/listings/{event.listing_id}')
    if kind in ('', 'import'):
        for event in db.query(SourceImportSnapshot).filter(SourceImportSnapshot.started_at >= since):
            append(event.started_at, 'import', 'Импорт · карантин' if event.blocked_by_schema_drift else 'Импорт реестра',
                   f'Принято {event.valid_rows} из {event.raw_rows}, отклонено {event.invalid_rows}')
    if kind in ('', 'scan'):
        for event in db.query(ScanRun).filter(ScanRun.started_at >= since):
            append(event.started_at, 'scan', f'Проверка {SOURCES[event.source.value]}',
                   f'{event.filters_ok}/{event.filters_total} фильтров · ошибок {event.technical_errors} · {event.network_profile}',
                   '/api/v1/dashboard/history')
    if kind in ('', 'feedback'):
        for event in db.query(FeedbackEvent).filter(FeedbackEvent.created_at >= since):
            append(event.created_at, 'feedback', f'Замечание · {event.actor or "Менеджер"}',
                   f'{event.to_status} · {event.note or ""}', '/api/v1/dashboard/feedback')
    events.sort(key=lambda item: item['at'], reverse=True)
    page = min(max(page, 1), max(1, (len(events) + 49) // 50))
    return {'events': events[(page - 1) * 50:page * 50], 'total': len(events), 'page': page,
            'pages': max(1, (len(events) + 49) // 50), 'days': days, 'kind': kind}
