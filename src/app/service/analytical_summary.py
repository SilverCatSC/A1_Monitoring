"""Automatic marketing brief from saved facts and explicit owner decisions.

No model/network call, source write, ad identity inference or reason for removal.
Decisions annotate observations; they never rewrite evidence or mark a cycle complete.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.service.artifact_bundle import publish_bundle
from app.service.content_review import content_claims
from app.service.coverage import absence_proven, coverage_status
from app.service.monitoring_digest import _link
from app.service.placement_feed_snapshot import FEED_SPECS
from app.service.placement_identity import parse_placement_id
from app.service.vehicle_dashboard import vehicle_dashboard
from app.service.workbook_identity import report_identity_conflicts

DECISIONS_FILE = 'monitoring_decisions.json'
MAX_BYTES = 4 * 1024 * 1024
GROUPS = {
    'price': ('Цены', 1), 'vat': ('Условия НДС', 1), 'year': ('Годы выпуска', 1),
    'placement': ('Ожидаемые публикации не найдены', 2),
    'identity': ('Идентификаторы объявлений', 2),
    'binding': ('Уточнить связь с таблицей', 3),
    'mileage': ('Пробег', 3), 'quality': ('Дополнить данные', 3),
}
IGNORE = {'feed_description_difference', 'identity_visual_alias', 'identity_operator_confirmed'}


def _json_file(path, root):
    path.resolve(strict=True).relative_to(root.resolve())
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError('symlink not allowed')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('file too large')
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def validate_decisions(payload, *, today=None):
    """Validate the whole registry; conflicting active scopes are not applied."""
    today = today or datetime.now(UTC).date()
    if (not isinstance(payload, dict) or payload.get('schema_version') != 1
            or not isinstance(payload.get('entries'), list) or len(payload['entries']) > 500):
        raise ValueError('invalid decisions schema')
    entries, seen, scopes, warnings = [], set(), {}, []
    for item in payload['entries']:
        if not isinstance(item, dict):
            raise ValueError('invalid decision')
        if not isinstance(item.get('active', True), bool):
            raise ValueError('invalid decision status')
        for key in ('id', 'unique_id', 'kind', 'basis', 'confirmed_by', 'recorded_on'):
            if not isinstance(item.get(key), str) or not item[key].strip() or len(item[key]) > 2000:
                raise ValueError('decision provenance required')
        if item['id'] in seen:
            raise ValueError('duplicate decision ID')
        seen.add(item['id'])
        if parse_placement_id(item['unique_id']).value != item['unique_id']:
            raise ValueError('exact unique_id required')
        recorded = date.fromisoformat(item['recorded_on'])
        if recorded > today:
            raise ValueError('future decision')
        if item['kind'] not in {'confirmed_year', 'placement_exception', 'open_question'}:
            raise ValueError('unsupported decision kind')
        source = item.get('source', 'all')
        if source not in {'all', 'auto_ru', 'avito'}:
            raise ValueError('invalid decision source')
        if item['kind'] == 'confirmed_year':
            if type(item.get('value')) is not int or not 1886 <= item['value'] <= 2100:
                raise ValueError('invalid confirmed year')
        elif item['kind'] == 'placement_exception' and (
            source == 'all' or not isinstance(item.get('partner'), str) or not item['partner'].strip()
        ):
            raise ValueError('partner exception requires one marketplace and partner')
        elif item['kind'] == 'open_question' and (
            not isinstance(item.get('question'), str) or not item['question'].strip()
            or len(item['question']) > 2000
        ):
            raise ValueError('open question text required')
        expired = item.get('valid_until') and date.fromisoformat(item['valid_until']) < today
        if not item.get('active', True):
            continue
        if expired:
            warnings.append('Истёк срок подтверждения ' + item['id'] + '; оно не применяется.')
            continue
        if item['kind'] == 'confirmed_year' and any(
            e['kind'] == 'confirmed_year' and e['unique_id'] == item['unique_id']
            and e['value'] != item['value'] for e in entries
        ):
            raise ValueError('conflicting confirmed years')
        entry = {**item, 'source': source}
        for src in ('auto_ru', 'avito') if source == 'all' else (source,):
            key = (item['unique_id'], item['kind'], src)
            if key in scopes:
                raise ValueError('overlapping decision scopes')
            scopes[key] = item['id']
        entries.append(entry)
    return {'schema_version': 1, 'status': 'available', 'entries': entries, 'warnings': warnings}


def load_decisions(evidence_dir):
    root = Path(evidence_dir).resolve()
    path = root / DECISIONS_FILE
    try:
        if not path.exists() and not path.is_symlink():
            return {'schema_version': 1, 'status': 'not_configured', 'entries': [], 'warnings': []}
        payload, digest = _json_file(path, root)
        return {**validate_decisions(payload), 'sha256': digest}
    except (OSError, ValueError, TypeError, KeyError):
        return {'schema_version': 1, 'status': 'invalid', 'entries': [], 'warnings': [
            'Реестр подтверждений недоступен, повреждён или противоречив. '
            'Исключения не применены; требуется проверка сопровождением мониторинга.',
        ]}


def _decision(policy, uid, kind, source):
    return next((d for d in policy.get('entries', []) if d['unique_id'] == uid
                 and d['kind'] == kind and d['source'] in {'all', source}), None)


def _number(value):
    return str(value or '').replace('\u00a0', ' ')


def _year_observations(card, raw, tile):
    values = []
    for key, label in [('year_attribute', 'Год в характеристиках'), ('year_title', 'Год в заголовке'), ('year', 'Год карточки')]:
        value = raw.get(key)
        if isinstance(value, int):
            values.append((label, value))
    title_years = set(re.findall(r'\b(?:19|20)\d{2}\b', raw.get('title') or ''))
    if len(title_years) == 1:
        values.append(('Год в заголовке', int(next(iter(title_years)))))
    values += [('Год в описании', value) for value in content_claims(raw.get('description'))['description_years']]
    if card.get('marketing_binding', {}).get('status') == 'matched':
        if str(tile.get('year', '')).isdigit():
            values.append(('Год в таблице маркетинга', int(tile['year'])))
        refs = card.get('reference_values', {}).get('year', {})
        for key, label in [('marketing', 'Год в таблице маркетинга'), ('feed', 'Год в листе фида')]:
            if isinstance(refs.get(key), int):
                values.append((label, refs[key]))
        # Compatibility with archived v1 reports before typed reference values.
        year_reference = next((f.get('reference', '') for f in card.get('fields', []) if f.get('label') == 'Год'), '')
        for ref in card.get('marketing_binding', {}).get('cells', []):
            text = str(ref.get('value', ''))
            if (f"{ref.get('sheet')}!{ref.get('a1')}:" in year_reference
                    and re.fullmatch(r'(?:19|20)\d{2}', text)):
                values.append((ref['sheet'] + '!' + ref['a1'], int(text)))
    return list(dict.fromkeys(values))


def build_analytical_summary(report, policy=None, *, previous=None, mode='cycle'):
    """Pure deterministic analysis; no changing the original report or identity."""
    policy = policy or {'schema_version': 1, 'status': 'not_configured', 'entries': [], 'warnings': []}
    content = report.get('content_review') or report.get('automatic_content_checks') or {}
    cards = content.get('cards', [])
    selected_sources = {row['source'] for row in coverage_status(report) if row['selected']}
    fleet = vehicle_dashboard(report, apply_brief=False)['vehicles']
    conflicts = report_identity_conflicts(content)
    by_uid = {v['unique_id']: v for v in fleet if v.get('unique_id') and not v.get('identity_conflict')}
    observed = {(c['source'], c['url']): c for c in report.get('observed_cards', [])}
    issues, limitations, exceptions = {}, [], []
    recovery = report.get('recovery') or {}
    if recovery.get('resume_of_cycle_id'):
        limitations.append({'title': 'Продолжение прерванной проверки', 'detail':
            f"Сохранённых поисковых фильтров использовано: {recovery.get('reused_search_filters', 0)}; "
            f"карточек: {recovery.get('reused_cards', 0)}. Их исходные даты наблюдения сохранены. "
            'Это продолжение проверки, а не новый полный обход всех страниц; полнота каталогов указана отдельно.'})
    previous_cases = {item['id']: item for brief in (previous or []) for item in brief.get('items', [])}

    def case(group, uid, title, source, url=None, *, identity_key=None, price=None):
        anchor = uid or identity_key or canonical_listing_key(EngineType(source), url) or f'{source}:{url}'
        token = hashlib.sha256(f'{group}|{anchor}'.encode()).hexdigest()[:20]
        if token not in issues:
            prior = previous_cases.get(token)
            issues[token] = {
                'id': token, 'group': group, 'unique_id': uid, 'vehicle': title or 'Объявление',
                'price': _number(price), 'sources': [], 'links': [], 'details': [], 'cells': [],
                'action': '', 'owner': 'Маркетинг', 'confirmations': [],
                'status': 'ongoing' if prior else 'observed',
                'first_detected_cycle_id': prior.get('first_detected_cycle_id') if prior else report['cycle_id'],
            }
        item = issues[token]
        if source not in item['sources']:
            item['sources'].append(source)
        if _link(url) and not any(link['url'] == url for link in item['links']):
            item['links'].append({'source': source, 'label': 'Auto.ru' if source == 'auto_ru' else 'Avito', 'url': url})
        return item

    for card in cards:
        source, url, uid = card['source'], card['url'], card.get('unique_id')
        tile = by_uid.get(uid, {})
        raw = observed.get((source, url), {}).get('card') or {}
        title = tile.get('title') or card.get('title')
        price = tile.get('price') or raw.get('price')
        binding = card.get('marketing_binding') or {}
        if uid in conflicts:
            item = case('identity', uid, 'Неоднозначный unique_id внутри книги', source, url)
            item['details'].append('Для одного ID есть неразрешённые повторы либо строки с противоречащими связями с маркетингом. Публикации не объединены с автомобилем; разные физические автомобили этим не доказаны.')
            item['action'] = 'Проверить ID и связи строк между фидами. Сначала устранить конфликт; не выбирать первую строку и не применять подтверждения к спорной связи.'
            continue
        if not uid:
            item = case('identity', None, title, source, url, price=price)
            item['details'].append('unique_id не подтверждён в прочитанной карточке; связь по цене, модели или VIN объявления не назначается.')
            if len(card.get('expected_workbook_ids', [])) == 1 and len(card.get('expected_marketing_rows', [])) == 1:
                item['details'].append(f"В плане размещения (строка {card['expected_marketing_rows'][0]}) "
                                       f"ожидается {card['expected_workbook_ids'][0]}. Это ожидание таблицы, "
                                       'а не прочитанный ID объявления; автоматическая привязка не выполнена.')
            item['action'] = 'Подрядчику проверить ID в актуальном подключённом фиде, полном описании и результате импорта; маркетингу подтвердить опубликованный unique_id.'
            item['owner'] = 'Маркетинг и подрядчик'
        elif binding.get('status') not in {None, 'matched'}:
            item = case('binding', uid, title, source, url, price=price)
            item['details'].append('ID опубликован, но однозначная связь с основной таблицей не установлена: ' + binding['status'] + '.')
            item['action'] = 'Проверить связь строк фида и основного листа внутри книги и актуальные ссылки. Если VIN ещё не присвоен, не выдумывать его; подтвердить другую однозначную связь источников.'
            item['cells'] += [r for r in binding.get('cells', []) if _link(r.get('url')) and len(r.get('value', '')) < 200]
            if tile.get('marketing_row'):
                item['details'].append(f"Для того же подтверждённого unique_id уже есть связь со строкой {tile['marketing_row']} через другую публикацию. Проверить актуальные ссылки; не требовать выдуманный VIN.")
        for finding in card.get('findings', []):
            code = finding.get('code', '')
            if code in IGNORE or code == 'marketing_identity_unresolved':
                continue
            group = next((field for field in ('price', 'vat', 'year', 'mileage') if field in code), None)
            difference = code.endswith(('_difference', '_conflict'))
            if not difference and code not in {'marketing_equipment_missing', 'generic_description', 'powertrain_as_equipment', 'published_id_missing'}:
                if code not in {'marketing_vin_empty', 'marketing_equipment_unverified'}:
                    limitations.append({'title': finding.get('title', code), 'detail': finding.get('detail', ''),
                                        'source': source, 'unique_id': uid, 'url': _link(url)})
                continue
            group = 'identity' if code == 'published_id_missing' else group if difference and group else 'quality'
            item = case(group, uid, title, source, url, price=price)
            text = finding.get('detail', '')
            if group == 'vat':
                for key, value in [('without', 'без НДС'), ('with', 'с НДС'), ('options', 'варианты с НДС/без НДС')]:
                    text = re.sub(r'\b' + key + r'\b', value, text)
            detail = {'source': source, 'code': code, 'text': text}
            if detail not in item['details']:
                item['details'].append(detail)
            item['action'] = ('Продажам подтвердить правильное значение по документам и условиям продажи; '
                              'маркетингу согласовать таблицу, фиды и публикации.') if difference else finding.get('action', 'Дополнить исходные данные.')
            if difference:
                item['owner'] = 'Маркетинг; продажи подтверждают значение'
            # Keep only useful source references; never copy full feed descriptions into a brief.
            for ref in binding.get('cells', []):
                if (_link(ref.get('url')) and len(ref.get('value', '')) < 200
                        and ref not in item['cells']):
                    item['cells'].append(ref)
            confirmation = _decision(policy, uid, 'confirmed_year', source) if group == 'year' else None
            if confirmation and confirmation not in item['confirmations']:
                item['confirmations'].append(confirmation)
                item['status'] = 'confirmed'
                item['action'] = (f"Подтверждённый год: {confirmation['value']}. Проверить документы и привести "
                                  'расходящиеся поля таблицы, фида и описаний к подтверждённому году. '
                                  'Старое замечание остаётся открытым, пока источники не согласованы.')
        confirmation = _decision(policy, uid, 'confirmed_year', source)
        if confirmation:
            values = _year_observations(card, raw, tile)
            differences = [(label, value) for label, value in values if value != confirmation['value']]
            if differences:
                item = case('year', uid, title, source, url, price=price)
                item['details'].append({'source': source, 'code': 'confirmed_year_difference',
                    'text': '; '.join(f'{label}: {value}' for label, value in differences)
                            + f". Подтверждено владельцем: {confirmation['value']}."})
                if confirmation not in item['confirmations']:
                    item['confirmations'].append(confirmation)
                item['status'] = 'confirmed'
                item['owner'] = 'Маркетинг; значение уже подтверждено владельцем'
            elif not values:
                limitations.append({'title': 'Нет полей для сверки подтверждённого года', 'source': source,
                                    'unique_id': uid, 'url': _link(url),
                                    'detail': 'Подтверждение сохранено, но отсутствующие значения не считаются совпадением.'})

    for sheet, structure in content.get('marketing_comparison', {}).get('feed_structure', {}).items():
        if len(structure.get('blocks', [])) <= 1 and not structure.get('merged_duplicates'):
            continue
        source = 'auto_ru' if sheet == 'autoru-feed-all' else 'avito'
        if source not in selected_sources:
            continue
        item = case('quality', None, 'Структура фида: ' + sheet, source, identity_key='feed_structure:' + sheet)
        item['details'].append(
            f"Блоков с собственными заголовками: {len(structure.get('blocks', []))}; "
            f"совместимых повторов ID объединено: {len(structure.get('merged_duplicates', []))}; "
            f"неоднозначных групп оставлено: {len(structure.get('unresolved_duplicates', []))}.")
        item['details'].append('Разные непустые значения повторных строк не выбраны автоматически. Объединение записей не подтверждает, какой блок фактически подключён к площадке.')
        fields = sorted({f for d in structure.get('merged_duplicates', []) for f in d['field_conflicts']})
        if fields:
            item['details'].append('Расходящиеся поля повторных строк: ' + ', '.join(fields) + '.')
        workbook = content.get('marketing_comparison', {}).get('workbook_url')
        if _link(workbook) and sheet in FEED_SPECS:
            item['cells'] = [{'sheet': sheet, 'a1': 'A' + str(block['header_row']),
                              'url': workbook.split('#')[0] + f'#gid={FEED_SPECS[sheet][0]}&range=A{block["header_row"]}'}
                             for block in structure.get('blocks', [])]
        item['action'] = 'Маркетингу и подрядчику согласовать действующую структуру и блок выгрузки. Проверить различающиеся поля повторных строк; не считать число повторов числом автомобилей.'
        item['owner'] = 'Маркетинг и подрядчик'

    for tile in fleet:
        uid = tile.get('unique_id')
        for platform in tile['platforms']:
            source, state = platform['source'], platform['state']
            if source not in selected_sources:
                continue
            if state == 'invalid_link':
                item = case('binding', uid, tile['title'], source,
                            identity_key=tile['key'], price=tile.get('price'))
                item['details'].append(platform['label'])
                item['action'] = 'Исправить ссылку в строке маркетинга: указать карточку соответствующей площадки. Не считать это снятием или отсутствием публикации.'
                if _link(tile.get('sheet_url')):
                    item['cells'].append({'sheet': 'Размещение_автомобилей', 'a1': 'строка ' + str(tile.get('marketing_row')), 'url': tile['sheet_url']})
                continue
            if state not in {'inactive', 'unknown'} or not platform.get('url'):
                continue
            if state == 'unknown':
                if not uid and platform['label'].startswith('Связь не подтверждена:'):
                    item = case('binding', uid, tile['title'], source, platform['url'],
                                identity_key=tile['key'], price=tile.get('price'))
                    item['details'].append(platform['label'])
                    item['action'] = ('Проверить актуальность ссылки и unique_id. Отсутствие автомобиля на площадке '
                                      'не доказано; не назначать замену по цене, VIN или модели объявления.')
                limitations.append({'title': tile['title'], 'source': source, 'unique_id': uid,
                                    'detail': platform['label'], 'url': platform['url']})
                continue
            decision = _decision(policy, uid, 'placement_exception', source)
            if decision:
                exceptions.append({'vehicle': tile['title'], 'unique_id': uid, 'source': source,
                                   'url': platform['url'], 'decision': decision,
                                   'detail': 'Публикация А1 отсутствует; учтено подтверждённое размещение у партнёра. '
                                             'Текущая активность объявления партнёра этим прогоном не проверялась.'})
                continue
            group = 'placement' if uid else 'binding'
            item = case(group, uid, tile['title'], source, platform['url'],
                        identity_key=tile['key'] if not uid else f'{uid}:{source}', price=tile.get('price'))
            item['details'].append('Указанная ссылка не найдена в полном активном каталоге. '
                                   'Причина и продажа автомобиля не установлены; перевыкладка без unique_id не исключена.')
            item['action'] = ('Проверить активные/архивные публикации, модерацию и результат импорта. '
                              'Подтвердить статус; при перевыкладке дать новую ссылку с тем же unique_id.') if uid else (
                                  'Проверить актуальность ссылки и связь с таблицей. Активные несопоставленные карточки '
                                  'могут относиться к этой строке; отсутствие автомобиля на площадке не доказано. '
                                  'Не назначать замену по цене или модели.')
            if _link(tile.get('sheet_url')):
                item['cells'].append({'sheet': 'Размещение_автомобилей',
                                      'a1': 'строка ' + str(tile.get('marketing_row')), 'url': tile['sheet_url']})

    # If the old format lacks workbook rows, keep historical signals visible without inventing expectations.
    if not content.get('marketing_vehicles'):
        limitations.append({'title': 'Нет сохранённого плана размещения', 'detail':
                            'Отсутствие ожидаемых публикаций по основной таблице здесь не пересчитано.'})
        for alert in report.get('publication_alerts', {}).get('items', []):
            if alert.get('kind') == 'publication_disappeared':
                limitations.append({'title': 'Исторический сигнал отсутствия публикации',
                                    'detail': alert.get('explanation', 'Требует актуального плана размещения.'),
                                    'unique_id': alert.get('unique_id'), 'url': _link(alert.get('old_url'))})
    for source in ('auto_ru', 'avito'):
        if source not in selected_sources:
            limitations.append({'title': 'Площадка не проверялась', 'source': source,
                                'detail': 'Площадка не входила в этот прогон; её текущие проблемы и активность не оценивались.'})
            continue
        if not absence_proven(report, source):
            limitations.append({'title': 'Полнота каталога не подтверждена', 'source': source,
                                'detail': 'Полнота каталога или проверки карточек/ID не подтверждена. Не делать вывод об исчезновении.'})
    for entry in policy.get('entries', []):
        if entry['kind'] != 'open_question':
            continue
        relevant = [c for c in cards if c.get('unique_id') == entry['unique_id'] and entry['unique_id'] not in conflicts
                    and entry['source'] in {'all', c['source']}]
        if not relevant:
            continue
        for card in relevant:
            tile = by_uid.get(entry['unique_id'], {})
            item = case('quality', entry['unique_id'], tile.get('title') or card.get('title'),
                        card['source'], card['url'], price=tile.get('price'))
            item['action'] = entry['question']
            detail = 'Ранее открытый вопрос, а не заново подтверждённое расхождение: ' + entry['basis']
            if detail not in item['details']:
                item['details'].append(detail)
            item['status'] = 'ongoing'
            item['question_provenance'] = entry
    for saved in report.get('observed_cards', []):
        positions = saved.get('supplemental_search_positions') or saved.get('search_positions') or []
        if saved.get('state') == 'active' and not any(p.get('page') or p.get('label') == '5+' for p in positions):
            limitations.append({'title': 'Позиция активной карточки не установлена',
                                'source': saved['source'], 'url': _link(saved['url']),
                                'detail': (saved.get('card') or {}).get('title', '')
                                + '. Уточнить фильтр или его применимость; это не доказательство неактивности.'})
    for item in issues.values():
        if item['confirmations']:
            year = item['confirmations'][0]['value']
            item['action'] = (f'Подтверждённый год: {year}. Проверить документы и согласовать '
                              'расходящиеся поля таблицы, фидов и описаний. '
                              'Известное замечание остаётся открытым, пока источники не согласованы.')
    items = sorted(issues.values(), key=lambda i: (GROUPS[i['group']][1], i['group'], i['vehicle'], i['id']))
    groups = [{'key': key, 'title': GROUPS[key][0], 'priority': GROUPS[key][1],
               'count': sum(i['group'] == key for i in items)} for key in GROUPS
              if any(i['group'] == key for i in items)]
    return {
        'schema_version': 1, 'cycle_id': report['cycle_id'], 'mode': mode,
        'generated_at_utc': datetime.now(UTC).isoformat(), 'observed_at_utc': report.get('created_at_utc'),
        'method': 'deterministic_saved_facts_with_explicit_decisions', 'source_writes': False,
        'decision_snapshot': policy, 'items': items, 'groups': groups,
        'exceptions': exceptions, 'limitations': limitations,
        'counts': {'issues': len(items), 'by_group': dict(Counter(i['group'] for i in items)),
                   'exceptions': len(exceptions), 'confirmed_cases': sum(bool(i['confirmations']) for i in items),
                   'ongoing': sum(i['status'] == 'ongoing' for i in items)},
        'active_counts': dict(Counter(c['source'] for c in report.get('observed_cards', []) if c.get('state') == 'active')),
        'marketing_read_at_utc': content.get('marketing_comparison', {}).get('read_finished_at_utc'),
    }


def summary_for_report(report, evidence_dir, *, mode='cycle'):
    existing = report.get('analytical_summary') or {}
    # Freeze the decision provenance of new cycles. Legacy reports explicitly use today's registry.
    policy = existing.get('decision_snapshot') if isinstance(existing, dict) else None
    if policy:
        try:
            if existing.get('schema_version') != 1 or existing.get('cycle_id') != report['cycle_id']:
                raise ValueError('saved summary belongs to another cycle')
            as_of = datetime.fromisoformat(existing['generated_at_utc'].replace('Z', '+00:00')).date()
            checked = validate_decisions(policy, today=as_of)
            policy = {**policy, 'entries': checked['entries']}
        except (ValueError, KeyError, TypeError, AttributeError):
            policy = {'schema_version': 1, 'status': 'invalid', 'entries': [], 'warnings': [
                'Сохранённое подтверждение не прошло проверку; исключения не применены.',
            ]}
    else:
        policy = load_decisions(evidence_dir)
    previous = []
    root = Path(evidence_dir).resolve()
    for cid in set(report.get('publication_alerts', {}).get('baseline_cycle_by_source', {}).values()):
        if cid == report['cycle_id'] or not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', cid):
            continue
        try:
            old, _digest = _json_file(root / 'cycles' / cid / 'placement_reconciliation.json', root)
            if old.get('cycle_id') != cid:
                continue
            previous.append(old.get('analytical_summary') or build_analytical_summary(old))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return build_analytical_summary(report, policy, previous=previous, mode=mode)


def render_marketing_text(summary):
    lines = [f"Сводка для маркетинга. Цикл {summary['cycle_id']}",
             f"Наблюдения: {summary.get('observed_at_utc')}; анализ: {summary['generated_at_utc']}",
             'Причины снятия и продажи не устанавливаются автоматически. Исправления выполняют маркетинг и подрядчик.', '']
    for group in summary['groups']:
        lines.append(group['title'] + ': ' + str(group['count']))
        for item in (i for i in summary['items'] if i['group'] == group['key']):
            lines += [f"• {item['vehicle']} — {item['price']} — {item['unique_id'] or 'unique_id не подтверждён'}",
                      '  ' + item['action']]
            lines += ['  ' + (d['text'] if isinstance(d, dict) else d) for d in item['details']]
            lines += ['  Подтверждение: ' + d['basis'] for d in item['confirmations']]
            lines += ['  ' + link['label'] + ': ' + link['url'] for link in item['links']]
        lines.append('')
    lines.append('Известные исключения:')
    lines += [f"• {e['vehicle']} ({e['source']}): {e['detail']} Основание: {e['decision']['basis']}" for e in summary['exceptions']]
    lines.append('Ограничения проверки:')
    lines += ['• ' + w for w in summary['decision_snapshot'].get('warnings', [])]
    lines += ['• ' + item['title'] + ': ' + item['detail'] for item in summary['limitations']]
    return '\n'.join(lines) + '\n'


def save_summary(summary, directory):
    publish_bundle(directory, {
        'analytical_summary.json': json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
        'marketing_summary.txt': render_marketing_text(summary),
    }, marker='analytical_summary.ready.json', commit_member='analytical_summary.json')
