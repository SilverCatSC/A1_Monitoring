"""Preflight seller membership and evidence-backed unique VIN relinking."""
import asyncio
import re
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import DealerListingCandidate, EngineType, Listing, ListingLinkOverride, ListingReconciliation
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.scraper.seller import SELLER_SOURCES, inspect_direct_link
from app.service.analytics import local_time, money
from app.service.dealer_discovery import DealerDiscoveryService
from app.service.filters import _listing_family
from app.service.listings import ListingRegistryService

LABELS = {'verified': 'Есть в каталоге продавца', 'review_required': 'Нужно проверить ссылку',
          'removed': 'Есть отметка о снятии / продаже', 'unavailable': 'Сверка недоступна',
          'missing_link': 'Нет ссылки'}


def check_key(listing_id, source):
    return f'{listing_id}:{source.value}'


def suggested_candidates(listing, source, candidates):
    family = _listing_family(listing, source)
    if not family:
        family = _listing_family(listing, EngineType.AVITO if source == EngineType.AUTO_RU else EngineType.AUTO_RU)
    if not family:
        return []
    result = []
    for candidate in candidates:
        probe = Listing(source_auto_ru=candidate.listing_url if source == EngineType.AUTO_RU else None,
                        source_avito=candidate.listing_url if source == EngineType.AVITO else None)
        if _listing_family(probe, source) != family:
            continue
        result.append({'id': candidate.id, 'url': candidate.listing_url, 'title': candidate.title,
                       'price': money(candidate.price_hint), 'basis': 'Совпадает семейство модели; автомобиль должен подтвердить человек'})
    return result[:30]


class SellerReconciliationService:
    def __init__(self, db, progress_callback=None, discovery=None, inspector=None):
        self.db = db
        self.progress = progress_callback or (lambda event: None)
        self.discovery = discovery or DealerDiscoveryService(db, progress_callback=progress_callback)
        self.inspector = inspector or inspect_direct_link
        self.inspections = {}
        self.candidate_checks = Counter()

    def inspect(self, source, url):
        key = (source, canonical_listing_key(source, url))
        if key not in self.inspections:
            self.inspections[key] = asyncio.run(self.inspector(source, url, self.progress))
        return self.inspections[key]

    def resolve_replacement(self, listing, source, candidates, listings, blocked):
        """Apply only a unique VIN match from freshly observed seller cards."""
        vin = (listing.vin or '').strip().upper()
        if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
            return None
        if sum((row.vin or '').strip().upper() == vin for row in listings) != 1:
            return None
        suggestions = suggested_candidates(listing, source, candidates)
        # Do not select a winner from a truncated candidate population.
        if not suggestions or len(suggestions) >= 30 or len(suggestions) > settings.seller_direct_checks_limit:
            return None
        field = 'source_auto_ru' if source == EngineType.AUTO_RU else 'source_avito'
        matches = []
        for candidate in suggestions:
            cache_key = (source, canonical_listing_key(source, candidate['url']))
            if cache_key not in self.inspections:
                if self.candidate_checks[source] >= settings.seller_direct_checks_limit:
                    return None
                self.candidate_checks[source] += 1
            inspection = self.inspect(source, candidate['url'])
            if inspection.get('state') == 'blocked':
                blocked.add(source.value)
                return None
            if inspection.get('state') != 'active':
                return None
            card_vin = (inspection.get('card', {}).get('vin') or '').strip().upper()
            if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', card_vin):
                return None
            if card_vin == vin:
                matches.append((candidate, inspection))
        if len(matches) != 1:
            return None
        candidate, inspection = matches[0]
        key = canonical_listing_key(source, candidate['url'])
        if any(row.id != listing.id and canonical_listing_key(source, getattr(row, field)) == key for row in listings):
            return None
        if not inspection.get('evidence'):
            return None
        old_url = getattr(listing, field)
        reason = 'Автоматическая сверка: единственный точный VIN в активной карточке свежего каталога дилера'
        ListingRegistryService(self.db).update_link(listing.id, source=source, url=candidate['url'],
                                                   actor='reconciliation', reason=reason)
        return {'old_url': old_url, 'url': candidate['url'], 'reason': reason, 'inspection': inspection}

    def run(self):
        self.inspections.clear()
        self.candidate_checks.clear()
        batch_id = str(uuid.uuid4())
        self.progress({'event': 'dealer_preflight_started', 'batch_id': batch_id})
        sources = {s: urls for s, urls in SELLER_SOURCES.items() if s in settings.scan_engines}
        discovery = self.discovery.run(sources=sources, strict=True)
        run_ids = set(discovery['run_ids'])
        blocked = set(discovery['blocked_sources'])
        fresh = [c for c in self.db.query(DealerListingCandidate).filter_by(active=True).all()
                 if (c.raw_payload or {}).get('discovery_run_id') in run_ids
                 and c.network_profile == settings.network_profile]
        listings = self.db.query(Listing).filter_by(is_active=True).order_by(Listing.id).all()
        checks = {}
        for source in EngineType:
            if source.value not in sources:
                continue
            candidates = [c for c in fresh if c.source == source]
            available = {c.external_key for c in candidates}
            counts = Counter(canonical_listing_key(source, row.source_auto_ru if source == EngineType.AUTO_RU else row.source_avito)
                             for row in listings)
            direct_checks = 0
            for listing in listings:
                url = listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
                key = canonical_listing_key(source, url)
                detail = {'discovery_run_ids': list(run_ids), 'network_profile': settings.network_profile}
                state, reason = 'verified', 'Текущий ID найден в свежем каталоге этого продавца'
                if source.value in blocked:
                    state, reason = 'unavailable', 'Площадка запросила проверку пользователя или ограничила доступ; обход остановлен'
                elif not is_marketplace_listing_url(source, url):
                    state, reason = 'missing_link', 'В реестре нет валидной прямой ссылки; требуется подтверждение'
                elif counts[key] > 1:
                    state, reason = 'review_required', 'Одна ссылка назначена нескольким автомобилям; связь нельзя считать однозначной'
                elif key not in available:
                    state, reason = 'review_required', 'ID не найден в просмотренной части каталога. Возможны перевыкладка, снятие или неполная загрузка'
                    if direct_checks < settings.seller_direct_checks_limit:
                        direct_checks += 1
                        inspection = self.inspect(source, url)
                        detail['direct_inspection'] = inspection
                        if inspection['state'] == 'removed':
                            state, reason = 'removed', inspection['reason'] + '; это не подтверждает продажу автомобиля компании'
                        elif inspection['state'] == 'blocked':
                            blocked.add(source.value)
                            state, reason = 'unavailable', 'Проверка старой ссылки остановлена защитой площадки'
                if state in ('review_required', 'removed', 'missing_link') and source.value not in blocked and counts[key] <= 1:
                    replacement = self.resolve_replacement(listing, source, candidates, listings, blocked)
                    if replacement:
                        detail['replacement'] = {k: v for k, v in replacement.items() if k != 'inspection'}
                        detail['direct_inspection'] = replacement['inspection']
                        url, state, reason = replacement['url'], 'verified', replacement['reason']
                    elif source.value in blocked:
                        state, reason = 'unavailable', 'Сверка кандидатов остановлена защитой площадки'
                suggestions = [] if state == 'verified' else suggested_candidates(listing, source, candidates)
                record = ListingReconciliation(batch_id=batch_id, listing_id=listing.id, source=source,
                    state=state, url=url, reason=reason, candidates=suggestions, details=detail, checked_at=datetime.now(UTC))
                self.db.add(record)
                self.db.flush()
                checks[check_key(listing.id, source)] = {'id': record.id, 'state': state, 'url': url, 'reason': reason}
        self.db.commit()
        summary = dict(Counter(c['state'] for c in checks.values()))
        self.progress({'event': 'dealer_preflight_finished', 'batch_id': batch_id, 'summary': summary})
        return {'batch_id': batch_id, 'checks': checks, 'blocked_sources': sorted(blocked), 'summary': summary,
                'discovery': discovery}

    def inspect_current_cards(self, preflight):
        """Inspect direct cards after search so content checks cannot block visibility collection."""
        records = (
            self.db.query(ListingReconciliation)
            .join(ListingReconciliation.listing)
            .filter(
                ListingReconciliation.batch_id == preflight['batch_id'],
                Listing.is_active.is_(True),
            )
            .order_by(ListingReconciliation.source, Listing.brand, Listing.model, Listing.id)
            .all()
        )
        blocked = set(preflight.get('blocked_sources', []))
        counters = Counter()
        checked_by_source = Counter()
        self.progress({'event': 'direct_cards_started', 'total': len(records)})
        total = len(records)
        for card_index, record in enumerate(records, 1):
            details = dict(record.details or {})
            if details.get('direct_inspection'):
                counters['already_checked'] += 1
                self.progress({
                    'event': 'direct_card_finished',
                    'source': record.source.value,
                    'card_index': card_index,
                    'card_total': total,
                    'vehicle': f'{record.listing.brand or ""} {record.listing.model or ""}'.strip(),
                    'status_code': (details['direct_inspection'].get('status_code')
                                    or details['direct_inspection'].get('state')),
                })
                continue
            url = record.listing.source_auto_ru if record.source == EngineType.AUTO_RU else record.listing.source_avito
            if not is_marketplace_listing_url(record.source, url):
                counters['missing_link'] += 1
                self.progress({
                    'event': 'direct_card_finished',
                    'source': record.source.value,
                    'card_index': card_index,
                    'card_total': total,
                    'vehicle': f'{record.listing.brand or ""} {record.listing.model or ""}'.strip(),
                    'status_code': 'missing_link',
                })
                continue
            if record.source.value in blocked:
                inspection = {
                    'state': 'blocked',
                    'status_code': 'skipped_after_block',
                    'reason': 'Не проверено после защиты площадки',
                }
            elif checked_by_source[record.source.value] >= settings.seller_detail_checks_limit:
                inspection = {
                    'state': 'unknown',
                    'status_code': 'limit_reached',
                    'reason': 'Достигнут защитный лимит прямых карточек за цикл',
                }
            else:
                checked_by_source[record.source.value] += 1
                inspection = asyncio.run(
                    self.inspector(
                        record.source,
                        url,
                        lambda event, current_index=card_index: self.progress({
                            **event,
                            'purpose': 'card_detail',
                            'card_index': current_index,
                            'card_total': total,
                        }),
                    )
                )
            details['direct_inspection'] = inspection
            record.details = details
            record.checked_at = datetime.now(UTC)
            code = inspection.get('status_code') or inspection.get('state') or 'unknown'
            counters[str(code)] += 1
            if inspection.get('state') == 'removed':
                record.state = 'removed'
                record.reason = inspection.get('reason', 'Объявление снято') + '; это не подтверждает продажу автомобиля компании'
            elif inspection.get('state') == 'blocked':
                blocked.add(record.source.value)
            check = preflight['checks'].get(check_key(record.listing_id, record.source))
            if check is not None and inspection.get('state') == 'removed':
                check.update(state=record.state, reason=record.reason)
            self.progress({
                'event': 'direct_card_finished',
                'source': record.source.value,
                'card_index': card_index,
                'card_total': total,
                'vehicle': f'{record.listing.brand or ""} {record.listing.model or ""}'.strip(),
                'status_code': code,
            })
        self.db.commit()
        summary = dict(counters)
        summary['checked'] = sum(checked_by_source.values())
        summary['total'] = len(records)
        summary['blocked_sources'] = sorted(blocked)
        self.progress({'event': 'direct_cards_finished', 'summary': summary})
        return summary


def reconciliation_context(db):
    ranked = db.query(ListingReconciliation.id, func.row_number().over(
        partition_by=(ListingReconciliation.listing_id, ListingReconciliation.source),
        order_by=(ListingReconciliation.checked_at.desc(), ListingReconciliation.id.desc()),
    ).label('rn')).subquery()
    records = db.query(ListingReconciliation).options(selectinload(ListingReconciliation.listing)).join(
        ranked, ranked.c.id == ListingReconciliation.id).filter(ranked.c.rn == 1).order_by(ListingReconciliation.checked_at.desc()).all()
    latest = {}
    for record in records:
        if record.listing.is_active:
            latest.setdefault(check_key(record.listing_id, record.source), record)
    overrides = {check_key(o.listing_id, o.source): o for o in db.query(ListingLinkOverride).all()}
    rows = []
    for key, record in latest.items():
        override = overrides.get(key)
        current = record.listing.source_auto_ru if record.source == EngineType.AUTO_RU else record.listing.source_avito
        changed = canonical_listing_key(record.source, current) != canonical_listing_key(record.source, record.url)
        direct = (record.details or {}).get('direct_inspection') or {}
        rows.append({'record': record, 'name': f'{record.listing.brand or ""} {record.listing.model or ""}'.strip(),
                     'vin': record.listing.vin or 'VIN не указан', 'time': local_time(record.checked_at),
                     'label': 'Ссылка изменена; ожидается сверка' if changed else LABELS[record.state],
                     'current_url': current, 'changed': changed, 'override': override,
                     'direct': direct,
                     'direct_proof': f'/api/v1/reconciliations/{record.id}/evidence' if direct.get('evidence') else None,
                     'conflict': bool(override and canonical_listing_key(record.source, override.last_source_url)
                                      != canonical_listing_key(record.source, override.url))})
    rows.sort(key=lambda row: (row['record'].state == 'verified', row['name'], row['record'].source.value))
    return {'rows': rows, 'total': len(rows), 'issues': sum(r['record'].state != 'verified' or r['changed'] for r in rows),
            'last_time': local_time(records[0].checked_at) if records else None,
            'sources': SELLER_SOURCES, 'labels': LABELS}
