"""Internal advert consistency, separate from unique_id/workbook reconciliation."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime

from app.models import EngineType
from app.scraper.base import is_marketplace_listing_url
from app.service.content_review import content_claims


def automatic_content_review(report, evidence_dir=None):
    cards = []
    all_findings = []
    for saved in report.get('observed_cards', []):
        if saved.get('state') != 'active' or saved.get('source') not in {'auto_ru', 'avito'}:
            continue
        if not is_marketplace_listing_url(EngineType(saved['source']), saved.get('url')):
            continue
        card = saved.get('card') or {}
        description = card.get('description') or ''
        claims = content_claims(description)
        findings = []

        def issue(code, title, detail, action, severity='warning', *, _findings=findings):
            _findings.append(
                {'code': code, 'title': title, 'detail': detail, 'action': action, 'severity': severity}
            )

        title_years = set(re.findall(r'\b(?:19|20)\d{2}\b', card.get('title') or ''))
        title_year = int(next(iter(title_years))) if len(title_years) == 1 else None
        attribute_year = card.get('year_attribute')
        page_years = {y for y in (title_year, attribute_year) if y is not None}
        if len(page_years) > 1:
            issue(
                'year_title_attribute_conflict',
                'Разные годы в заголовке и характеристиках',
                f'Заголовок: {title_year}; характеристики: {attribute_year}.',
                'Уточнить год по документам и привести поля к одному значению.',
            )
        if claims['description_years'] and page_years and set(claims['description_years']) != page_years:
            issue(
                'year_description_conflict',
                'Год в описании расходится с карточкой',
                f'Карточка: {sorted(page_years)}; описание: {claims["description_years"]}.',
                'Подтвердить правильный год по документам; не выводить его из unique_id.',
            )
        prices = {
            int(re.sub(r'\D', '', m))
            for m in re.findall(
                r'^\s*(?:цена|стоимость)\s*[:—-]\s*(\d[\d\s\u00a0\u202f]*)\s*(?:₽|руб(?:лей|ля|\.)?)',
                description,
                re.I | re.M,
            )
        }
        price = card.get('price')
        if len(prices) > 1:
            issue(
                'description_prices_ambiguous',
                'В описании указано несколько цен',
                str(sorted(prices)),
                'Уточнить условия цен; не выбирать вариант автоматически.',
            )
        elif prices and price is not None and int(price) not in prices:
            issue(
                'price_description_conflict',
                'Цена в описании расходится с ценой карточки',
                f'Карточка: {price}; описание: {next(iter(prices))}.',
                'Проверить актуальную цену и условия оплаты.',
            )
        mileage = card.get('mileage_km')
        if mileage is not None and claims['mileage_km'] and set(claims['mileage_km']) != {mileage}:
            issue(
                'mileage_description_conflict',
                'Разный пробег в характеристиках и описании',
                f'Характеристики: {mileage} км; описание: {claims["mileage_km"]} км.',
                'Проверить актуальный пробег и исправить расхождение.',
            )
        attribute_vat = content_claims(card.get('vat_attribute'))['vat']
        if (
            attribute_vat in {'with', 'without'}
            and claims['vat'] in {'with', 'without'}
            and attribute_vat != claims['vat']
        ):
            issue(
                'vat_description_conflict',
                'Разные условия НДС',
                f'Характеристики: {card.get("vat_attribute")}; описание: {claims["vat"]}.',
                'Уточнить условия реализации и цену для выбранного режима НДС.',
            )
        if claims['vat'] == 'options':
            issue(
                'vat_options',
                'Указаны варианты оплаты с НДС и без НДС',
                'Варианты не считаются автоматически противоречием.',
                'Проверить, понятно ли, к какому варианту относится цена.',
                'info',
            )
        if card.get('vat_required') and not (attribute_vat or claims['vat']):
            issue(
                'vat_unread',
                'Условия НДС не прочитаны',
                'В характеристиках и описании не найдено однозначного условия НДС.',
                'Проверить условия сделки; не присваивать режим НДС по умолчанию.',
            )
        if not description.strip():
            issue(
                'description_missing',
                'Описание отсутствует',
                'Текст описания не прочитан.',
                'Проверить наличие индивидуального описания.',
            )
        elif claims['generic_description']:
            issue(
                'generic_description',
                'Описание похоже на общий шаблон',
                'Не выявлено достаточного индивидуального описания машины.',
                'Проверить и дополнить фактические сведения о машине.',
            )
        if claims['powertrain_in_equipment_label']:
            issue(
                'powertrain_as_equipment',
                'Вместо комплектации указан тип силовой установки',
                'Обнаружено поле «Комплектация: Последовательный гибрид».',
                'Указать комплектацию отдельно от силовой установки.',
            )
        capture = card.get('description_capture') or {}
        full = (
            capture.get('software_truncated') is False
            and capture.get('sha256') == hashlib.sha256(description.encode()).hexdigest()
        )
        if description and not full:
            issue(
                'description_capture_unverified',
                'Полнота сохранённого описания не подтверждена',
                'Нет проверяемого признака полного текста.',
                'Дочитать описание; не считать проверку содержания завершённой.',
            )
        for field in card.get('attribute_ambiguities') or []:
            issue(
                'attribute_ambiguous',
                'Параметр карточки неоднозначен',
                str(field),
                'Проверить исходное поле; значение автоматически не выбирается.',
            )
        captured = None
        if evidence_dir:
            from app.service.evidence import EvidenceAccessError, read_direct_card_evidence

            try:
                captured = read_direct_card_evidence(saved, str(evidence_dir))['captured_at']
            except EvidenceAccessError:
                issue(
                    'content_evidence_unverified',
                    'Доказательство карточки не подтверждено',
                    'Снимок недоступен, повреждён или не подтверждает именно эту карточку.',
                    'Восстановить доказательство перед окончательным выводом.',
                )

        def verdict(codes, _findings=findings):
            return (
                'Требует проверки'
                if any(f['code'] in codes for f in _findings)
                else 'Расхождение не установлено; проверены только доступные поля'
            )

        fields = [
            {
                'label': 'Цена',
                'observed': str(price) if price is not None else 'Не прочитана',
                'reference': 'Явная цена в описании: ' + str(sorted(prices) or 'не указана'),
                'verdict': verdict({'price_description_conflict', 'description_prices_ambiguous'}),
            },
            {
                'label': 'Год',
                'observed': f'Заголовок: {title_year}; характеристики: {attribute_year}',
                'reference': 'Год в описании: ' + str(claims['description_years'] or 'не указан'),
                'verdict': verdict({'year_title_attribute_conflict', 'year_description_conflict'}),
            },
            {
                'label': 'Пробег',
                'observed': str(mileage) + ' км' if mileage is not None else 'Не прочитан',
                'reference': 'Пробег в описании: ' + str(claims['mileage_km'] or 'не указан'),
                'verdict': verdict({'mileage_description_conflict'}),
            },
            {
                'label': 'Комплектация',
                'observed': card.get('factory_equipment') or 'Заводская комплектация не прочитана',
                'reference': 'Индивидуальный салон: '
                + ', '.join(claims['custom_packages'] or ['не распознан']),
                'verdict': 'Заводская комплектация и переоборудование не приравниваются; нужна отдельная сверка с источником',
            },
            {
                'label': 'НДС',
                'observed': card.get('vat_attribute') or 'Отдельное поле не прочитано',
                'reference': 'В описании: ' + str(claims['vat'] or 'не указано'),
                'verdict': verdict({'vat_description_conflict', 'vat_unread'}),
            },
            {
                'label': 'Описание',
                'observed': str(len(description)) + ' символов',
                'reference': 'Полный текст подтверждён контрольной суммой'
                if full
                else 'Полнота не подтверждена',
                'verdict': verdict(
                    {
                        'description_missing',
                        'generic_description',
                        'description_capture_unverified',
                        'powertrain_as_equipment',
                    }
                ),
            },
        ]
        item = {
            'source': saved['source'],
            'url': saved['url'],
            'unique_id': card.get('placement_id') or saved.get('unique_id'),
            'title': card.get('title') or saved['url'],
            'fields': fields,
            'findings': findings,
            'captured_at_utc': captured or 'Время снимка отдельно не проверено',
            'source_cells': ['Характеристики и описание сохранённой карточки'],
            'identity_note': 'Это проверка внутри карточки. VIN и цена не используются для назначения unique_id.',
            'description_note': 'С таблицей маркетинга на этом этапе не сравнивалось.',
            'owners_raw': card.get('owners_raw'),
            'owners_count': card.get('owners_count'),
            'owners_minimum': card.get('owners_minimum'),
        }
        cards.append(item)
        all_findings.extend(findings)
    groups = []
    for code in dict.fromkeys(f['code'] for f in all_findings):
        members = [f for f in all_findings if f['code'] == code]
        first = members[0]
        groups.append(
            {
                'title': first['title'],
                'detail': f'Карточек: {len(members)}. ' + first['detail'],
                'action': first['action'],
            }
        )
    return {
        'schema_version': 1,
        'cycle_id': report['cycle_id'],
        'created_at_utc': datetime.now(UTC).isoformat(),
        'new_live_scan': False,
        'source_writes': False,
        'scope': 'Автоматическая проверка внутренних противоречий сохранённых активных карточек. Сверка с маркетинговой таблицей — отдельный этап.',
        'cards': cards,
        'issue_groups': groups,
        'summary': {
            'cards_checked': len(cards),
            'cards_with_warnings': sum(any(f['severity'] == 'warning' for f in c['findings']) for c in cards),
            'codes': dict(Counter(f['code'] for f in all_findings)),
        },
        'limitations': 'Отсутствие найденного противоречия не означает подтверждённую корректность. Неизвестные поля не заполняются догадками. Автоматическая проверка не подтверждает соответствие документам и актуальной таблице маркетинга.',
    }
