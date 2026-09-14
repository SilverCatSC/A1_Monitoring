"""Business-readable queue derived from current marketplace reconciliation facts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models import DealerListingCandidate, EngineType, Listing, ListingReconciliation
from app.scraper.base import canonical_listing_key, evidence_manifest_name

SEVERITY_ORDER = {'high': 0, 'medium': 1, 'low': 2}


def offer_review_queue(db: Session) -> dict[str, Any]:
    """Explain current Offer inconsistencies without changing any relationship.

    This is intentionally a read-only derived queue.  A marketplace result can
    support a finding, but never changes Listing/Vehicle/Offer ownership by itself.
    """
    records = _latest_active_reconciliations(db)
    findings: list[dict[str, Any]] = []
    expected_keys: dict[EngineType, set[str]] = defaultdict(set)
    discovery_run_ids: dict[EngineType, set[str]] = defaultdict(set)

    for record in records:
        listing = record.listing
        url = _listing_url(listing, record.source)
        key = canonical_listing_key(record.source, url)
        if key:
            expected_keys[record.source].add(key)
        details = record.details or {}
        discovery_run_ids[record.source].update(
            item for item in details.get('discovery_run_ids', []) if isinstance(item, str)
        )
        findings.extend(_record_findings(record))

    for candidate in db.query(DealerListingCandidate).filter(
        DealerListingCandidate.active.is_(True)
    ):
        run_id = (candidate.raw_payload or {}).get('discovery_run_id')
        if run_id not in discovery_run_ids[candidate.source]:
            continue
        if candidate.external_key in expected_keys[candidate.source]:
            continue
        raw = candidate.raw_payload or {}
        page_evidence = raw.get('page_evidence')
        if (
            isinstance(page_evidence, str)
            and raw.get('page_evidence_manifest') == evidence_manifest_name(page_evidence)
        ):
            findings.append(
                _finding(
                    code='ghost_offer',
                    severity='high',
                    source=candidate.source,
                    listing=None,
                    reconciliation=None,
                    summary='В свежем каталоге продавца есть публикация, которой нет в активном реестре Monitoring',
                    action='Проверить принадлежность публикации. При подтверждении связать её вручную; не назначать автоматически.',
                    values={
                        'candidate_title': candidate.title,
                        'candidate_price': candidate.price_hint,
                        'candidate_url': candidate.listing_url,
                        'external_key': candidate.external_key,
                        'dealer_url': candidate.dealer_url,
                    },
                    proof_url=f'/api/v1/dealer/candidates/{candidate.id}/evidence',
                )
            )
        else:
            findings.append(
                _finding(
                    code='ghost_offer_evidence_missing',
                    severity='medium',
                    source=candidate.source,
                    listing=None,
                    reconciliation=None,
                    summary='Свежая публикация каталога не имеет проверяемого evidence; принадлежность не подтверждена',
                    action='Повторить каталог площадки и получить screenshot до обработки публикации.',
                    values={'external_key': candidate.external_key},
                )
            )

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER[item['severity']],
            item['source'],
            item.get('listing_name') or '',
            item['code'],
        )
    )
    by_severity = Counter(item['severity'] for item in findings)
    by_code = Counter(item['code'] for item in findings)
    return {
        'schema_version': 1,
        'generated_at': datetime.now(UTC).isoformat(),
        'summary': {
            'total': len(findings),
            'by_severity': dict(by_severity),
            'by_code': dict(by_code),
            'active_listing_source_checks': len(records),
            'fresh_discovery_sources': sorted(source.value for source, ids in discovery_run_ids.items() if ids),
        },
        'findings': findings,
    }


def _latest_active_reconciliations(db: Session) -> list[ListingReconciliation]:
    rows = (
        db.query(ListingReconciliation)
        .options(selectinload(ListingReconciliation.listing))
        .order_by(ListingReconciliation.checked_at.desc(), ListingReconciliation.id.desc())
        .all()
    )
    latest: dict[tuple[str, EngineType], ListingReconciliation] = {}
    for record in rows:
        if not record.listing.is_active:
            continue
        latest.setdefault((record.listing_id, record.source), record)
    return list(latest.values())


def _record_findings(record: ListingReconciliation) -> list[dict[str, Any]]:
    listing = record.listing
    direct = (record.details or {}).get('direct_inspection') or {}
    findings: list[dict[str, Any]] = []

    if record.state == 'missing_link':
        findings.append(
            _finding(
                code='missing_offer_link',
                severity='high',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Активный автомобиль не имеет валидной прямой ссылки на площадке',
                action='Найти публикацию и подтвердить связь оператором.',
            )
        )
        return findings
    if record.state == 'review_required':
        code = 'replacement_candidate' if record.candidates else 'offer_not_verified_in_catalogue'
        findings.append(
            _finding(
                code=code,
                severity='high',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary=(
                    'Свежий каталог предлагает возможную перевыкладку, но связь не доказана'
                    if record.candidates
                    else 'Текущий Offer не найден в просмотренной части свежего каталога'
                ),
                action=(
                    'Сравнить VIN, комплектацию и фотографии; подтвердить только точный Offer.'
                    if record.candidates
                    else 'Проверить прямую карточку и каталог; не считать это продажей автомобиля.'
                ),
                values={'candidates': record.candidates[:10]},
            )
        )
    elif record.state == 'unavailable':
        findings.append(
            _finding(
                code='catalogue_check_unavailable',
                severity='medium',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Каталог площадки не дал проверяемого ответа',
                action='Повторить контролируемую проверку после снятия технического ограничения.',
            )
        )

    if not direct:
        return findings
    status = direct.get('status_code') or direct.get('state') or 'unknown'
    proof = direct.get('evidence')
    if not isinstance(proof, str) or direct.get('evidence_manifest') != evidence_manifest_name(proof):
        proof = None
    if not proof:
        findings.append(
            _finding(
                code='direct_evidence_missing',
                severity='medium',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Состояние прямой карточки не имеет сохранённого доказательства',
                action='Повторить прямую проверку; не использовать текущий статус для бизнес-решения.',
                values={'reported_status': status},
            )
        )
        return findings
    if status in {'removed', 'sold', 'unpublished', 'closed'}:
        findings.append(
            _finding(
                code='missing_offer',
                severity='high',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Прямая карточка показывает снятие или продажу предложения',
                action='Проверить, нужна ли перевыкладка. Это не доказывает продажу автомобиля компании.',
                values={'reported_status': status, 'reason': direct.get('reason')},
                proof=proof,
            )
        )
        return findings
    if status != 'active':
        findings.append(
            _finding(
                code='direct_check_incomplete',
                severity='medium',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Прямая карточка не дала подтверждённого активного статуса',
                action='Повторить проверку после устранения технической причины.',
                values={'reported_status': status, 'reason': direct.get('reason')},
                proof=proof,
            )
        )
        return findings

    card = direct.get('card') if isinstance(direct.get('card'), dict) else {}
    if _different_money(listing.price_hint, card.get('price')):
        findings.append(
            _finding(
                code='price_mismatch',
                severity='high',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Цена активной прямой карточки расходится с ценой в реестре Monitoring',
                action='Проверить источник маркетинга и карточку; зафиксировать корректную цену в уполномоченном источнике.',
                values={'registry_price': listing.price_hint, 'card_price': card.get('price')},
                proof=proof,
            )
        )
    if listing.year and card.get('year') and listing.year != card['year']:
        findings.append(
            _finding(
                code='year_mismatch',
                severity='medium',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='Год в активной прямой карточке расходится с реестром Monitoring',
                action='Проверить точность связи Offer и данные источника.',
                values={'registry_year': listing.year, 'card_year': card.get('year')},
                proof=proof,
            )
        )
    if listing.vin and card.get('vin') and listing.vin.strip().upper() != card['vin'].strip().upper():
        findings.append(
            _finding(
                code='vin_mismatch',
                severity='high',
                source=record.source,
                listing=listing,
                reconciliation=record,
                summary='VIN активной карточки не совпадает с VIN реестра',
                action='Остановить автоматические предположения и подтвердить принадлежность карточки оператором.',
                values={'registry_vin': listing.vin, 'card_vin': card.get('vin')},
                proof=proof,
            )
        )
    if record.source == EngineType.AVITO:
        vat = card.get('vat_status')
        if vat is None:
            findings.append(
                _finding(
                    code='vat_not_disclosed',
                    severity='medium',
                    source=record.source,
                    listing=listing,
                    reconciliation=record,
                    summary='В описании активной карточки Avito не найден явный статус НДС',
                    action='Проверить описание карточки и дополнить источник; превью поиска недостаточно.',
                    proof=proof,
                )
            )
        elif vat == 'Неоднозначно':
            findings.append(
                _finding(
                    code='vat_ambiguous',
                    severity='high',
                    source=record.source,
                    listing=listing,
                    reconciliation=record,
                    summary='В описании активной карточки Avito одновременно встречаются противоречивые признаки НДС',
                    action='Проверить текст и юридически корректный статус НДС у владельца данных.',
                    values={'card_vat': vat},
                    proof=proof,
                )
            )
    return findings


def _finding(
    *,
    code: str,
    severity: str,
    source: EngineType,
    listing: Listing | None,
    reconciliation: ListingReconciliation | None,
    summary: str,
    action: str,
    values: dict[str, Any] | None = None,
    proof: str | None = None,
    proof_url: str | None = None,
) -> dict[str, Any]:
    return {
        'code': code,
        'severity': severity,
        'source': source.value,
        'listing_id': listing.id if listing else None,
        'listing_name': _listing_name(listing) if listing else None,
        'reconciliation_id': reconciliation.id if reconciliation else None,
        'summary': summary,
        'action': action,
        'values': values or {},
        'proof': proof_url or (
            f'/api/v1/reconciliations/{reconciliation.id}/evidence'
            if proof and reconciliation is not None
            else None
        ),
    }


def _listing_url(listing: Listing, source: EngineType) -> str | None:
    return listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito


def _listing_name(listing: Listing | None) -> str | None:
    if listing is None:
        return None
    return ' '.join(item for item in (listing.brand, listing.model) if item) or listing.id


def _different_money(left: float | None, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        return round(float(left)) != round(float(right))
    except (TypeError, ValueError):
        return False
