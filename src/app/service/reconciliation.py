"""Preflight seller membership. Similarity suggests candidates, never changes a VIN binding."""
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import (
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    Listing,
    ListingLinkOverride,
    ListingReconciliation,
)
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.scraper.browser_session import run_browser_task
from app.scraper.seller import SELLER_SOURCES, inspect_direct_link
from app.service.analytics import local_time, money
from app.service.dealer_discovery import DealerDiscoveryService
from app.service.filters import _listing_family
from app.service.offer_reconciliation import offer_review_queue
from app.service.placement_status import LABELS as PLACEMENT_LABELS
from app.service.placement_status import record_placement_status
from app.service.republication_review import exact_republication_candidate

LABELS = {'verified': 'Есть в каталоге продавца', 'review_required': 'Нужно проверить ссылку',
          'removed': 'Есть отметка о снятии / продаже', 'unavailable': 'Сверка недоступна',
          'missing_link': 'Нет ссылки'}


def check_key(listing_id, source):
    return f'{listing_id}:{source.value}'


def fresh_catalogue_candidates(db, discovery):
    """Only positive cards observed by this cycle, never cached old inventory."""
    run_ids = set(discovery.get('run_ids', []))
    return [candidate for candidate in db.query(DealerListingCandidate).filter_by(active=True).all()
            if (candidate.raw_payload or {}).get('discovery_run_id') in run_ids
            and candidate.network_profile == settings.network_profile
            and is_marketplace_listing_url(candidate.source, candidate.listing_url)]


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
    def __init__(
        self,
        db,
        progress_callback=None,
        discovery=None,
        inspector=None,
        cycle_id: str | None = None,
    ):
        self.db = db
        self.cycle_id = cycle_id
        self.progress = progress_callback or (lambda event: None)
        self.discovery = discovery or DealerDiscoveryService(
            db, progress_callback=progress_callback, cycle_id=cycle_id
        )
        self.inspector = inspector or inspect_direct_link

    def run(self):
        batch_id = str(uuid.uuid4())
        self.progress({'event': 'dealer_preflight_started', 'batch_id': batch_id})
        sources = {s: urls for s, urls in SELLER_SOURCES.items() if s in settings.scan_engines}
        discovery = self.discovery.run(sources=sources, strict=True)
        run_ids = set(discovery['run_ids'])
        blocked = set(discovery['blocked_sources'])
        fresh = fresh_catalogue_candidates(self.db, discovery)
        runs = self.db.query(DealerDiscoveryRun).filter(DealerDiscoveryRun.id.in_(run_ids)).all()
        source_failures = {}
        for run in runs:
            if run.complete:
                continue
            diagnostics = run.diagnostics or {}
            failure = {
                'source': run.source.value, 'run_id': run.id,
                'error': run.error or 'Catalogue end not proven',
                'blocked': run.source.value in blocked,
                'technical_error': bool(run.error and not run.error.startswith('Catalogue end not proven')),
                'evidence': next((value for key, value in diagnostics.items()
                                  if key.endswith('_evidence')), None),
                'http_status': next((value for key, value in diagnostics.items()
                                     if key.endswith('_http_status') and isinstance(value, int) and value >= 400), None),
            }
            source_failures.setdefault(run.source.value, []).append(failure)
        coverage = {source: bool([r for r in runs if r.source == source])
                    and all(r.complete for r in runs if r.source == source) for source in EngineType}
        listings = self.db.query(Listing).filter_by(is_active=True).order_by(Listing.id).all()
        checks = {}
        for source in EngineType:
            if source.value not in sources:
                continue
            candidates = [c for c in fresh if c.source == source]
            available = {canonical_listing_key(source, c.listing_url): c for c in candidates}
            counts = Counter(canonical_listing_key(source, row.source_auto_ru if source == EngineType.AUTO_RU else row.source_avito)
                             for row in listings)
            for listing in listings:
                url = listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
                key = canonical_listing_key(source, url)
                detail = {'discovery_run_ids': list(run_ids), 'network_profile': settings.network_profile,
                          'catalogue_complete': coverage[source], 'registry_url': url,
                          'card_check_policy': 'active_catalogue_only'}
                state, reason = 'verified', 'Текущий ID найден в свежем каталоге этого продавца'
                if source.value in blocked:
                    state, reason = 'unavailable', 'Площадка запросила проверку пользователя или ограничила доступ; обход остановлен'
                elif not is_marketplace_listing_url(source, url):
                    state, reason = 'missing_link', 'В реестре нет валидной прямой ссылки; требуется подтверждение'
                elif counts[key] > 1:
                    state, reason = 'review_required', 'Одна ссылка назначена нескольким автомобилям; связь нельзя считать однозначной'
                elif key not in available:
                    state, reason = 'review_required', 'ID не найден в просмотренной части каталога. Возможны перевыкладка, снятие или неполная загрузка'
                    if coverage[source]:
                        reason = 'Сохранённый ID не найден в полном активном каталоге. Требуется сверка снятия или перевыкладки'
                if state == 'verified':
                    # Same marketplace ID, freshly observed URL: avoid stale paths.
                    url = available[key].listing_url
                    detail['active_catalogue_url'] = url
                else:
                    detail['inspection_skip_reason'] = (
                        'source_unavailable' if state == 'unavailable' else
                        'link_missing' if state == 'missing_link' else
                        'identity_ambiguous' if counts[key] > 1 else
                        'catalogue_incomplete' if not coverage[source] else
                        'not_in_active_catalogue'
                    )
                    if source.value in source_failures:
                        detail['source_failures'] = source_failures[source.value]
                suggestions = [] if state == 'verified' else suggested_candidates(listing, source, candidates)
                record = ListingReconciliation(cycle_id=self.cycle_id, batch_id=batch_id, listing_id=listing.id, source=source,
                    state=state, url=url, reason=reason, candidates=suggestions, details=detail, checked_at=datetime.now(UTC))
                business_status = record_placement_status(record, detail['registry_url'])
                if business_status:
                    record.reason = PLACEMENT_LABELS[business_status]
                    reason = record.reason
                self.db.add(record)
                self.db.flush()
                checks[check_key(listing.id, source)] = {'id': record.id, 'state': state, 'url': url, 'reason': reason,
                                                      'placement_status': business_status,
                                                      'skip_reason': detail.get('inspection_skip_reason'),
                                                      'source_failures': detail.get('source_failures', [])}
        self.db.commit()
        summary = dict(Counter(c['state'] for c in checks.values()))
        self.progress({'event': 'dealer_preflight_finished', 'batch_id': batch_id, 'summary': summary})
        return {'cycle_id': self.cycle_id, 'batch_id': batch_id, 'checks': checks, 'blocked_sources': sorted(blocked), 'summary': summary,
                'discovery': discovery, 'source_failures': source_failures, 'active_card_inspections': {}}

    def inspect_current_cards(self, preflight):
        """Inspect direct cards after search so content checks cannot block visibility collection."""
        records = (
            self.db.query(ListingReconciliation)
            .join(ListingReconciliation.listing)
            .filter(
                ListingReconciliation.batch_id == preflight['batch_id'],
                Listing.is_active.is_(True),
            )
            .order_by(ListingReconciliation.checked_at.desc(), ListingReconciliation.id.desc())
            .all()
        )
        # Automatic ID link synchronization adds a fresh check for the new URL.
        # Keep the old check as history, but inspect only the current one.
        latest = {}
        for record in records:
            latest.setdefault((record.listing_id, record.source), record)
        records = sorted(latest.values(), key=lambda record: (
            record.source.value, record.listing.brand or '', record.listing.model or '', record.listing_id,
        ))
        fresh = {(candidate.source, canonical_listing_key(candidate.source, candidate.listing_url)): candidate
                 for candidate in fresh_catalogue_candidates(self.db, preflight.get('discovery', {}))}
        eligible = []
        for record in records:
            current_url = record.listing.source_auto_ru if record.source == EngineType.AUTO_RU else record.listing.source_avito
            key = (record.source, canonical_listing_key(record.source, current_url))
            if (record.state == 'verified' and key in fresh
                    and canonical_listing_key(record.source, record.url) == key[1]):
                eligible.append((record, fresh[key]))
        skipped_not_in_catalogue = sum(
            (record.source, canonical_listing_key(record.source, record.url)) not in fresh
            and (record.details or {}).get('inspection_skip_reason') not in {'source_unavailable', 'catalogue_incomplete'}
            for record in records
        )
        skipped_source_unavailable = sum(record.state == 'unavailable' for record in records)
        skipped_catalogue_incomplete = sum(
            (record.details or {}).get('inspection_skip_reason') == 'catalogue_incomplete' for record in records
        )
        skipped_unresolved = (len(records) - len(eligible) - skipped_not_in_catalogue
                              - skipped_source_unavailable - skipped_catalogue_incomplete)
        blocked = set(preflight.get('blocked_sources', []))
        counters = Counter()
        checked_by_source = Counter()
        reused = 0
        cache = preflight.setdefault('active_card_inspections', {})
        total = len(eligible)
        self.progress({'event': 'direct_cards_started', 'total': total,
                       'skipped_not_in_catalogue': skipped_not_in_catalogue,
                       'skipped_unresolved': skipped_unresolved})
        for card_index, (record, candidate) in enumerate(eligible, 1):
            details = dict(record.details or {})
            url = candidate.listing_url
            key = (record.source, canonical_listing_key(record.source, url))
            inspection = cache.get(key) or details.get('direct_inspection')
            if inspection is not None:
                reused += 1
            elif record.source.value in blocked:
                inspection = {
                    'state': 'blocked', 'status_code': 'skipped_after_block',
                    'reason': 'Не проверено после защиты площадки',
                }
            elif checked_by_source[record.source.value] >= settings.seller_detail_checks_limit:
                inspection = {
                    'state': 'unknown', 'status_code': 'limit_reached',
                    'reason': 'Достигнут защитный лимит прямых карточек за цикл',
                }
            else:
                checked_by_source[record.source.value] += 1
                inspection = run_browser_task(self.inspector(
                    record.source, url,
                    lambda event, current_index=card_index: self.progress({
                        **event, 'purpose': 'card_detail',
                        'card_index': current_index, 'card_total': total,
                    }),
                ))
                cache[key] = inspection
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
                'event': 'direct_card_finished', 'source': record.source.value,
                'card_index': card_index, 'card_total': total,
                'vehicle': f'{record.listing.brand or ""} {record.listing.model or ""}'.strip(),
                'status_code': code,
            })
        self.db.commit()
        summary = dict(counters)
        resolved_codes = {'active', 'removed', 'sold', 'unpublished', 'closed'}
        processed = sum(counters.values())
        resolved = sum(count for code, count in counters.items() if code in resolved_codes)
        review_required = counters.get('missing_link', 0)
        incomplete = max(0, total - resolved)
        summary['checked'] = sum(checked_by_source.values())
        summary['reused'] = reused
        summary['skipped_not_in_catalogue'] = skipped_not_in_catalogue
        summary['skipped_unresolved'] = skipped_unresolved
        summary['skipped_source_unavailable'] = skipped_source_unavailable
        summary['skipped_catalogue_incomplete'] = skipped_catalogue_incomplete
        summary['total'] = total
        summary['processed'] = processed
        summary['completed'] = resolved
        summary['incomplete'] = incomplete
        summary['technical_errors'] = max(0, incomplete - review_required)
        summary['review_required'] = review_required
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
        business_status = record_placement_status(record, current)
        rows.append({'record': record, 'name': f'{record.listing.brand or ""} {record.listing.model or ""}'.strip(),
                     'vin': record.listing.vin or 'VIN не указан', 'time': local_time(record.checked_at),
                     'label': PLACEMENT_LABELS.get(business_status) or ('Ссылка изменена; ожидается сверка' if changed else LABELS[record.state]),
                     'placement_status': business_status,
                     'current_url': current, 'changed': changed, 'override': override,
                     'direct': direct,
                     'id_candidate': exact_republication_candidate(record),
                     'direct_proof': f'/api/v1/reconciliations/{record.id}/evidence' if direct.get('evidence') else None,
                     'conflict': bool(override and canonical_listing_key(record.source, override.last_source_url)
                                      != canonical_listing_key(record.source, override.url))})
    rows.sort(key=lambda row: (row['record'].state == 'verified', row['name'], row['record'].source.value))
    review_queue = offer_review_queue(db)
    return {'rows': rows, 'total': len(rows), 'issues': sum(
                r['placement_status'] != 'not_expected' and (r['record'].state != 'verified' or r['changed']) for r in rows),
            'last_time': local_time(records[0].checked_at) if records else None,
            'sources': SELLER_SOURCES, 'labels': LABELS, 'review_queue': review_queue}
