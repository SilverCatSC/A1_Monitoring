"""Read-only content comparison: advert unique_id -> feed -> workbook VIN join.

An advert's VIN, price, URL and model never establish publication identity.
"""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.service.content_identity import resolve_content_identity
from app.service.content_review import content_claims
from app.service.feed_photos import feed_photo_candidates
from app.service.placement_feed_snapshot import FEED_SPECS
from app.service.placement_identity import PlacementIdError, parse_placement_id
from app.service.vehicle_content import vin_values
from app.service.vehicle_dashboard import marketing_vehicle_snapshot
from app.service.workbook_identity import workbook_identity_conflicts

MAIN = 'Размещение_автомобилей'
SPECS = {
    'auto_ru': {
        'sheets': ['autoru-feed-all'],
        'id': 'unique_id',
        'vin': 'vin',
        'price': 'price',
        'year': 'year',
        'mileage': 'run',
        'vat': 'with_nds',
        'description': 'description',
        'owners': 'owners_number',
    },
    'avito': {
        'sheets': ['avito-feed-new', 'avito-feed-used'],
        'id': 'Id',
        'vin': 'VIN',
        'price': 'Price',
        'year': 'Year',
        'mileage': 'Kilometrage',
        'description': None,
        'owners': 'Owners',
    },
}


def number(value):
    text = str(value if value is not None else '').strip()
    if not re.fullmatch(r'\d[\d\s\u00a0\u202f]*(?:\s*(?:₽|руб\.?|км\.?))?', text, re.I):
        return None
    return int(re.sub(r'\D', '', text))


def column_name(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _empty_vin_publication_reference(snapshot, uid, source, url):
    """Link a workbook row only AFTER the advert ID was independently proven.

    An exact feed AvitoId -> column X, or a previously declared Auto.ru URL
    -> column W, can identify a VIN-empty row. No price/model/ad VIN is used.
    """
    anchors, refs = [], []
    for sheet in ('avito-feed-new', 'avito-feed-used'):
        records = snapshot.rows_by_sheet.get(sheet, [])
        for row, feed in enumerate(records, snapshot.first_data_rows.get(sheet, 4)):
            platform_id = feed.get('AvitoId', '').strip()
            if feed.get('Id', '').strip() != uid or not platform_id.isdigit():
                continue
            owners = [r for sh in ('avito-feed-new', 'avito-feed-used')
                      for r in snapshot.rows_by_sheet.get(sh, []) if r.get('AvitoId', '').strip() == platform_id]
            if len(owners) != 1:
                return [], []
            anchors.append(('avito', 'avito:' + platform_id))
            for key in ('Id', 'AvitoId'):
                for loc in snapshot.cell_locations(sheet, row, key, list(feed).index(key) + 1):
                    refs.append((sheet, loc['column'], loc['row'], feed[key]))
    if source == 'auto_ru':
        key = canonical_listing_key(EngineType.AUTO_RU, url)
        if key:
            anchors.append(('auto_ru', key))
    uid_vins = set()
    for records in snapshot.rows_by_sheet.values():
        for feed in records:
            if (feed.get('unique_id') or feed.get('Id') or '').strip() == uid:
                value = feed.get('vin') or feed.get('VIN') or ''
                vins = vin_values(value)
                if str(value).strip() and len(vins) != 1:
                    return [], []
                uid_vins.update(vins)
    rows, vin_rows = [], []
    for number, values in enumerate(snapshot.marketing_rows[1:], 2):
        if len(values) <= 23 or values[5].strip().casefold() != 'актуально':
            continue
        if any(canonical_listing_key(EngineType(src), values[22 if src == 'auto_ru' else 23]) == key
               for src, key in anchors):
            rows.append((number, values))
        if vin_values(values[10]) & uid_vins:
            vin_rows.append((number, values))
    if rows:
        rows = sorted({number: values for number, values in rows + vin_rows}.items())
    # A populated competing row must participate in ambiguity detection. Only
    # after that check may this narrow fallback accept one VIN-empty row.
    if len(rows) == 1 and rows[0][1][10].strip():
        return [], refs
    return rows, refs


def add_marketing_comparison(
    review, observed_cards, snapshot, *, evidence_dir=None, operator_confirmed_ids=None
):
    """Enrich the six-field content schema, keeping all internal findings."""
    meta = {
        'status': 'available' if snapshot.marketing_sha256 else 'unavailable',
        'workbook_url': snapshot.workbook_url,
        'read_started_at_utc': snapshot.read_started_at_utc,
        'read_finished_at_utc': snapshot.read_finished_at_utc,
        'sha256_by_sheet': {**snapshot.sha256_by_sheet, MAIN: snapshot.marketing_sha256},
        'error': snapshot.marketing_error,
        'feed_structure': {sheet: {key: value for key, value in info.items() if key not in {'cell_sources', 'field_values'}}
                           for sheet, info in snapshot.feed_structure.items()},
        'identity_rule': 'Объявление → подтверждённый unique_id (с отдельным основанием) → строка фида → VIN внутри книги → строка маркетинга.',
    }
    review['marketing_comparison'] = meta
    conflicts = workbook_identity_conflicts(snapshot)
    review['identity_conflicts'] = conflicts
    intrinsic_checks = {id(item): (deepcopy(item['fields']), deepcopy(item['findings'])) for item in review['cards']}
    observed = {(c['source'], c['url']): c for c in observed_cards}
    bindings = Counter()

    def cell(sheet, col, row, value):
        gid = 0 if sheet == MAIN else FEED_SPECS[sheet][0]
        a1 = f'{column_name(col)}{row}'
        return {
            'sheet': sheet,
            'a1': a1,
            'value': str(value or ''),
            'url': f'{snapshot.workbook_url}#gid={gid}&range={a1}',
        }

    for item in review['cards']:
        saved = observed.get((item['source'], item['url']), {})
        card = saved.get('card') or {}
        resolution = resolve_content_identity(
            saved, snapshot, evidence_dir=evidence_dir, operator_confirmed_ids=operator_confirmed_ids
        )
        item['identity_resolution'] = resolution
        uid = resolution['unique_id']
        if uid:
            item['unique_id'] = uid
        refs = []
        item['marketing_binding'] = {'status': 'unavailable', 'cells': refs}
        item['identity_note'] = meta['identity_rule'] + ' VIN и цена объявления не назначают ID. Ссылки используются для проверки номера площадки и связей внутри книги.'
        item['description_note'] = (
            'Сохранённая карточка сравнивается с книгой на дату чтения; это не новый осмотр площадки.'
        )

        def finding(code, title, detail, action, severity='warning', *, _item=item):
            _item['findings'].append(
                {'code': code, 'title': title, 'detail': detail, 'action': action, 'severity': severity}
            )

        def unresolved(status, detail, *, _item=item):
            _item['marketing_binding']['status'] = status
            bindings[status] += 1
            finding(
                'marketing_identity_unresolved',
                'Сверка с маркетингом не завершена',
                detail,
                'Уточнить данные источника; не назначать объявлению идентификатор по VIN, цене или ссылке.',
            )
            for field in _item['fields']:
                field['reference'] += ' · Маркетинг: ' + detail

        if not snapshot.marketing_sha256:
            unresolved('snapshot_unavailable', snapshot.marketing_error or 'Актуальная таблица не прочитана.')
            continue
        try:
            if not isinstance(uid, str) or parse_placement_id(uid).value != uid:
                raise PlacementIdError('not exact')
        except PlacementIdError:
            # A declared URL identifies the workbook row to ask about, never
            # the advert's unpublished ID. Preserve this as an expectation only.
            expected_rows, expected_ids = [], set()
            for main_row_no, values in enumerate(snapshot.marketing_rows[1:], 2):
                col = 22 if item['source'] == 'auto_ru' else 23
                if (len(values) <= col or values[5].strip().casefold() != 'актуально'
                        or canonical_listing_key(EngineType(item['source']), values[col])
                        != canonical_listing_key(EngineType(item['source']), item['url'])):
                    continue
                expected_rows.append(main_row_no)
                for records in snapshot.rows_by_sheet.values():
                    for record in records:
                        if vin_values(record.get('vin') or record.get('VIN')) & vin_values(values[10]):
                            expected_id = (record.get('unique_id') or record.get('Id') or '').strip()
                            try:
                                exact = parse_placement_id(expected_id).value == expected_id
                            except PlacementIdError:
                                exact = False
                            if exact and expected_id not in conflicts:
                                expected_ids.add(expected_id)
            item['expected_workbook_ids'] = sorted(expected_ids)
            item['expected_marketing_rows'] = expected_rows
            item['expected_id_is_publication_binding'] = False
            unresolved(
                resolution['status'], 'Связь по unique_id не подтверждена: ' + resolution['status'] + '.'
            )
            continue
        spec = SPECS[item['source']]
        matches = [
            (sheet, row, values)
            for sheet in spec['sheets']
            for row, values in enumerate(
                snapshot.rows_by_sheet.get(sheet, []), snapshot.first_data_rows.get(sheet, 1)
            )
            if values.get(spec['id'], '').strip() == uid
        ]
        if len(matches) != 1:
            unresolved(
                'feed_id_missing' if not matches else 'feed_id_duplicate',
                f'Строк фида с точным unique_id: {len(matches)}.',
            )
            continue
        sheet, fr, feed = matches[0]
        headers = list(feed)

        def feed_cell(key, *, _feed=feed, _headers=headers, _sheet=sheet, _row=fr, _refs=refs):
            if key and key in _headers:
                locations = snapshot.cell_locations(_sheet, _row, key, _headers.index(key) + 1)
                variants = snapshot.field_values(_sheet, _row, key, _feed.get(key))
                references = [cell(_sheet, loc['column'], loc['row'],
                                   variants[loc['variant']] if 'variant' in loc else _feed.get(key))
                              for loc in locations]
                _refs.extend(references)
                return references[0] if references else None
            return None

        feed_cell(spec['id'])
        feed_cell(spec['vin'])
        vins = vin_values(feed.get(spec['vin']))
        row_basis = 'workbook_vin'
        if len(vins) == 1:
            vin = next(iter(vins))
            rows = [
                (row, values)
                for row, values in enumerate(snapshot.marketing_rows[1:], 2)
                if len(values) > 10
                and vin in vin_values(values[10])
                and values[5].strip().casefold() == 'актуально'
            ]
        elif not str(feed.get(spec['vin']) or '').strip() and item['source'] == 'avito':
            # The publication ID was established before this step. This is only
            # an intra-workbook link from feed AvitoId to marketing column X.
            platform_id = str(feed.get('AvitoId') or '').strip()
            siblings = [
                r
                for sh in spec['sheets']
                for r in snapshot.rows_by_sheet.get(sh, [])
                if str(r.get('AvitoId') or '').strip() == platform_id
            ]
            if not platform_id.isdigit() or len(siblings) != 1:
                unresolved(
                    'feed_vin_ambiguous', 'VIN пуст; нет единственного AvitoId для связи строк внутри книги.'
                )
                continue
            feed_cell('AvitoId')
            rows = [
                (row, values)
                for row, values in enumerate(snapshot.marketing_rows[1:], 2)
                if len(values) > 23
                and values[5].strip().casefold() == 'актуально'
                and canonical_listing_key(EngineType.AVITO, values[23]) == 'avito:' + platform_id
            ]
            row_basis = 'workbook_avito_id'
        elif not str(feed.get(spec['vin']) or '').strip() and uid not in conflicts:
            rows, reference_cells = _empty_vin_publication_reference(snapshot, uid, item['source'], item['url'])
            if not rows:
                unresolved('feed_vin_ambiguous', 'VIN пуст; однозначная ссылка внутри книги не установлена.')
                continue
            refs.extend(cell(*ref) for ref in reference_cells)
            row_basis = 'workbook_confirmed_id_reference'
        else:
            unresolved('feed_vin_ambiguous', 'В строке фида нет одного полного VIN для связи внутри книги.')
            continue
        if not rows and len(vins) == 1 and uid not in conflicts:
            reference_rows, reference_cells = _empty_vin_publication_reference(snapshot, uid, item['source'], item['url'])
            if reference_rows:
                rows = reference_rows
                refs.extend(cell(*ref) for ref in reference_cells)
                row_basis = 'workbook_confirmed_id_reference'
        if len(rows) != 1:
            if len(rows) > 1 and row_basis == 'workbook_confirmed_id_reference':
                conflicts[uid] = {'unique_id': uid, 'code': 'workbook_reference_conflict',
                                  'marketing_rows': [n for n, _ in rows],
                                  'feed_rows': [{'sheet': sheet, 'row': fr}]}
            unresolved(
                'marketing_row_missing' if not rows else 'marketing_row_duplicate',
                f'Актуальных строк маркетинга по выбранному ключу внутри книги ({row_basis}): {len(rows)}.',
            )
            continue
        if uid in conflicts:
            unresolved('workbook_identity_conflict',
                       'Один unique_id противоречиво указан в строках фидов/основного листа; связь не назначена.')
            finding('identity_conflict', 'Противоречивые связи unique_id внутри книги',
                    'Строки маркетинга: ' + str(conflicts[uid]['marketing_rows']),
                    'Проверить unique_id и связи строк между фидами; не выбирать первую запись.')
            continue
        mr, values = rows[0]

        def main(col, *, _values=values, _row=mr, _refs=refs):
            value = _values[col - 1].strip() if col <= len(_values) else ''
            _refs.append(cell(MAIN, col, _row, value))
            return value

        main(11)
        main(6)
        main(23 if item['source'] == 'auto_ru' else 24)
        item['marketing_binding'].update(
            status='matched',
            unique_id=uid,
            feed_sheet=sheet,
            feed_row=fr,
            marketing_row=mr,
            row_basis=row_basis,
        )
        merged = next((d for d in snapshot.feed_structure.get(sheet, {}).get('merged_duplicates', [])
                       if d['rows'][0] == fr), None)
        if merged:
            item['marketing_binding']['feed_duplicate_rows'] = merged['rows']
            for name in ('price', 'year', 'mileage', 'vat', 'owners'):
                key = spec.get(name)
                if key and key in merged['field_conflicts']:
                    finding(f'feed_{name}_conflict', 'Разные значения в повторных строках фида',
                            f'{sheet}, строки {merged["rows"]}, поле {key}: '
                            + str(snapshot.field_values(sheet, fr, key, ''))
                            + '. Значение не выбрано автоматически.',
                            'Маркетингу подтвердить действующее значение и согласовать повторные строки.')
        if row_basis == 'workbook_avito_id':
            finding(
                'marketing_vin_empty',
                'VIN для связи строк внутри книги не заполнен',
                f'{sheet}: VIN пуст. Строка маркетинга связана по единственному AvitoId фида и X{mr}. '
                'unique_id объявления подтверждён отдельно по его тексту и фиду.',
                'Проверить и заполнить VIN в источнике; цену и VIN объявления для сопоставления не использовали.',
                'info',
            )
        elif row_basis == 'workbook_confirmed_id_reference':
            finding('marketing_vin_empty', 'VIN основной строки не заполнен',
                    f'Размещение_автомобилей!K{mr}: VIN пуст. unique_id объявления подтверждён независимо; '
                    'строка книги связана по единственной ссылке публикации/AvitoId. Цена, модель и VIN объявления не использованы.',
                    'При появлении VIN заполнить его в основной строке и фидах; не выдумывать VIN для снятия предупреждения.', 'info')
        bindings['matched'] += 1
        item['unique_id'] = uid
        item['marketing_binding']['basis'] = resolution['basis']
        if resolution['basis'] in {'visual_alias', 'operator_confirmed'}:
            detail = (
                'ID в сохранённом тексте содержит похожие кириллические буквы. '
                'Числовая часть не менялась; единственная строка и описание фида подтверждают ASCII unique_id.'
                if resolution['basis'] == 'visual_alias'
                else 'В сохранённом описании ID отсутствует. Использовано ранее явное подтверждение владельца, '
                'повторно проверенное по сохранённым данным и актуальному AvitoId фида.'
            )
            finding(
                'identity_' + resolution['basis'],
                'Способ подтверждения unique_id',
                detail,
                'Исходный текст сохранён; это не точное чтение ASCII ID из текущей карточки.',
                'info',
            )
            item['identity_note'] += ' Основание: ' + detail
            if resolution['basis'] == 'operator_confirmed':
                finding('published_id_missing', 'unique_id отсутствует в сохранённом описании',
                        f'Связь с {uid} подтверждена владельцем и повторно проверена. '
                        'Это не устраняет отсутствие ID в тексте текущей публикации.',
                        f'Маркетингу и подрядчику добавить явно подписанный unique_id {uid} в описание и проверить результат импорта.')
        claims = content_claims(card.get('description'))
        year = card.get('year_attribute') or card.get('year_title')
        if year is None:
            years = set(re.findall(r'\b(?:19|20)\d{2}\b', card.get('title') or ''))
            year = int(next(iter(years))) if len(years) == 1 else None
        mileage = card.get('mileage_km')
        if mileage is None and len(claims['mileage_km']) == 1:
            mileage = claims['mileage_km'][0]
        for index, key, col, actual in [
            (0, 'price', 12, number(card.get('price'))),
            (1, 'year', 13, year),
            (2, 'mileage', 14, mileage),
        ]:
            expected_raw = main(col)
            expected = number(expected_raw)
            feed_key = spec[key]
            feed_ref = feed_cell(feed_key)
            feed_value = number(feed.get(feed_key))
            item.setdefault('reference_values', {})[key] = {'marketing': expected, 'feed': feed_value}
            field = item['fields'][index]
            field['reference'] += f' · {MAIN}!{column_name(col)}{mr}: {expected_raw or "не заполнено"}'
            if feed_ref:
                field['reference'] += f' · {sheet}!{feed_ref["a1"]}: {feed.get(feed_key) or "не заполнено"}'
            if expected is None or actual is None:
                field['verdict'] += ' · Маркетинг: недостаточно данных для сравнения.'
            elif actual == expected:
                field['verdict'] += ' · С основным листом совпадает.'
            else:
                field['verdict'] += ' · Расхождение с основным листом.'
                finding(
                    'marketing_' + key + '_difference',
                    'Расхождение: ' + field['label'],
                    f'Карточка: {actual}; {MAIN}!{column_name(col)}{mr}: {expected_raw}.',
                    'Подтвердить актуальное значение и исправить источник или публикацию.',
                )
            if expected is not None and feed_value is not None and feed_value != expected:
                finding(
                    'feed_' + key + '_difference',
                    'Лист фида расходится с маркетингом: ' + field['label'],
                    f'{sheet}!{feed_ref["a1"]}: {feed_value}; {MAIN}!{column_name(col)}{mr}: {expected_raw}.',
                    'Проверить формирование фида и результат последнего импорта; подключённый XML здесь не проверялся.',
                )
        vat = main(16)
        expected_vat = content_claims(vat)['vat']
        actual_vat = content_claims(card.get('vat_attribute'))['vat'] or claims['vat']
        item['fields'][4]['reference'] += f' · {MAIN}!P{mr}: {vat or "не заполнено"}'
        item['fields'][4]['verdict'] += (
            ' · С основным листом совпадает.'
            if actual_vat == expected_vat and actual_vat in {'with', 'without'}
            else ' · НДС с маркетингом не подтверждён однозначно.'
        )
        if (
            actual_vat in {'with', 'without'}
            and expected_vat in {'with', 'without'}
            and actual_vat != expected_vat
        ):
            finding(
                'marketing_vat_difference',
                'Расходятся условия НДС',
                f'Карточка: {actual_vat}; {MAIN}!P{mr}: {vat}.',
                'Подтвердить условия реализации и цену.',
            )
        vat_key = spec.get('vat')
        vat_ref = feed_cell(vat_key)
        feed_vat = {'true': 'with', 'false': 'without'}.get(str(feed.get(vat_key) or '').lower())
        if vat_ref and expected_vat in {'with', 'without'} and feed_vat and feed_vat != expected_vat:
            finding(
                'feed_vat_difference',
                'НДС в листе фида расходится с маркетингом',
                f'{sheet}!{vat_ref["a1"]}: {feed_vat}; {MAIN}!P{mr}: {vat}.',
                'Проверить формирование фида и условия реализации.',
            )
        equipment = main(10)
        expected_packages = set(content_claims(equipment)['custom_packages'])
        actual_packages = set(claims['custom_packages'])
        item['fields'][3]['reference'] += f' · {MAIN}!J{mr}: {equipment or "не заполнено"}'
        if expected_packages and expected_packages == actual_packages:
            item['fields'][3]['verdict'] += ' · Название пакета А1 совпало; состав опций не подтверждён.'
        elif not equipment:
            finding(
                'marketing_equipment_missing',
                'Не заполнена комплектация А1 в маркетинге',
                f'{MAIN}!J{mr} пустая.',
                'Уточнить пакет переоборудования у владельца данных.',
            )
        else:
            finding(
                'marketing_equipment_unverified',
                'Комплектацию А1 нужно сверить',
                f'{MAIN}!J{mr}: {equipment}; распознано в описании: {sorted(actual_packages)}.',
                'Проверить индивидуальное описание и спецификацию; заводскую комплектацию не приравнивать к салону А1.',
                'info',
            )
        description_key = spec.get('description') or ('Description' if 'Description' in feed else None)
        feed_cell(description_key)
        feed_text = feed.get(description_key) or ''
        if feed_text:
            from bs4 import BeautifulSoup

            def normal(text):
                return ' '.join(BeautifulSoup(text, 'html.parser').get_text(' ', strip=True).split())

            same = normal(card.get('description') or '') == normal(feed_text)
            item['fields'][5]['reference'] += f' · Полное описание {sheet}: ' + (
                'совпало' if same else 'текст отличается'
            )
            if not same:
                finding(
                    'feed_description_difference',
                    'Описание отличается от листа фида',
                    'Полные тексты после нормализации пробелов и HTML различаются; маскирование VIN и оформление тоже могут отличаться.',
                    'Проверить содержательные изменения; отличие текста само по себе не доказывает ошибку.',
                    'info',
                )
        expected_owners = number(main(15))
        actual_owners = card.get('owners_count')
        if expected_owners is not None and actual_owners is not None and expected_owners != actual_owners:
            finding(
                'marketing_owners_difference',
                'Расходится число владельцев',
                f'Карточка: {actual_owners}; {MAIN}!O{mr}: {expected_owners}.',
                'Проверить документы и исправить источник или публикацию.',
            )
        item['source_cells'] += [r['sheet'] + '!' + r['a1'] for r in refs]
    # A later card can reveal contradictory workbook references for an ID
    # already compared earlier in this pass. Invalidate the whole disputed join,
    # retaining intrinsic advert checks and the underlying source references.
    for item in review['cards']:
        uid, binding = item.get('unique_id'), item.get('marketing_binding', {})
        if uid in conflicts and binding.get('status') == 'matched':
            binding['status'] = 'workbook_identity_conflict'
            binding['invalidated_marketing_row'] = binding.pop('marketing_row', None)
            item['fields'], item['findings'] = intrinsic_checks[id(item)]
            item.pop('reference_values', None)
            item['findings'].append({'code': 'identity_conflict', 'title': 'Противоречащие ссылки строк книги',
                                     'detail': 'Привязка по этому ID отменена: другая карточка выявила неоднозначные ссылки строк.',
                                     'action': 'Проверить ссылки и связи строк; сравнение с выбранной строкой не подтверждено.',
                                     'severity': 'warning'})
    bindings = Counter(item.get('marketing_binding', {}).get('status') for item in review['cards'])
    all_findings = [f for c in review['cards'] for f in c['findings']]
    review['issue_groups'] = []
    for code in dict.fromkeys(f['code'] for f in all_findings):
        members = [f for f in all_findings if f['code'] == code]
        review['issue_groups'].append(
            {
                'title': members[0]['title'],
                'detail': f'Карточек: {len(members)}. ' + members[0]['detail'],
                'action': members[0]['action'],
            }
        )
    review['summary']['identity_bases'] = dict(
        Counter(
            c['marketing_binding'].get('basis')
            for c in review['cards']
            if c['marketing_binding']['status'] == 'matched'
        )
    )
    review['summary'].update(
        marketing_bindings=dict(bindings),
        cards_with_warnings=sum(
            any(f['severity'] == 'warning' for f in c['findings']) for c in review['cards']
        ),
        codes=dict(Counter(f['code'] for f in all_findings)),
        highlights=[
            f'Сохранённых активных карточек: {len(review["cards"])}; сопоставлено по unique_id: {bindings["matched"]}; '
            f'без однозначной связи: {len(review["cards"]) - bindings["matched"]}.'
        ],
    )
    review['scope'] = (
        'Проверка содержания сохранённых карточек и автоматическая сверка с оригинальной таблицей маркетинга по unique_id.'
    )
    review['limitations'] = (
        'Снимки объявлений и книга имеют собственные даты. Сравнение не подтверждает фактическую комплектацию и документы. '
        'XML, реально подключённый к площадке, не проверялся. Отсутствие расхождения в доступных полях не означает полноту проверки.'
    )
    review['marketing_vehicles'] = marketing_vehicle_snapshot(snapshot, review['cards'])
    review['feed_photo_candidates'] = feed_photo_candidates(review, observed_cards, snapshot)
    return review
