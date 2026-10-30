"""Vehicle tiles from saved evidence; VIN joins workbook rows, never adverts."""

import re
from collections import defaultdict

from app.models import EngineType
from app.scraper.base import canonical_listing_key, evidence_manifest_name, is_marketplace_listing_url
from app.service.company_site_photos import apply_company_photo
from app.service.coverage import absence_proven
from app.service.feed_photos import safe_feed_photo_url
from app.service.monitoring_digest import _link
from app.service.vehicle_content import vin_values
from app.service.workbook_identity import report_identity_conflicts, workbook_identity_conflicts

MAIN = 'Размещение_автомобилей'


def marketing_vehicle_snapshot(snapshot, verified_cards=()):
    """Small display projection retained with the content report for future cycles."""
    if not snapshot.marketing_sha256:
        return []
    feeds = defaultdict(list)
    conflicts = workbook_identity_conflicts(snapshot)
    for sheet, rows in snapshot.rows_by_sheet.items():
        for row in rows:
            uid = (row.get('unique_id') or row.get('Id') or '').strip()
            if uid:
                feeds[uid].append((sheet, row))
    result = []
    for number, raw in enumerate(snapshot.marketing_rows[1:], 2):
        row = list(raw) + [''] * 30
        if row[5].strip().casefold() != 'актуально':
            continue
        vins = vin_values(row[10])
        matching_rows = [r for r in snapshot.marketing_rows[1:]
                         if len(r) > 10 and r[5].strip().casefold() == 'актуально'
                         and vin_values(r[10]) & vins]
        # Ambiguous workbook joins remain separate display rows.
        ids, images = [], []
        if len(vins) == 1 and len(matching_rows) == 1:
            for uid, entries in feeds.items():
                if uid in conflicts:
                    continue
                entry_vins = [vin_values(r.get('vin') or r.get('VIN')) for _, r in entries]
                source_counts = defaultdict(int)
                for sheet, _ in entries:
                    source_counts['auto_ru' if sheet == 'autoru-feed-all' else 'avito'] += 1
                if any(n > 1 for n in source_counts.values()):
                    continue
                if any(v == vins for v in entry_vins) and all(not v or v == vins for v in entry_vins):
                    ids.append(uid)
                    for _, r in entries:
                        images += re.findall(r'https://[^\s<>"|]+', r.get('images') or r.get('ImageUrls') or r.get('Images') or '')
        # More than one UID for a row is unresolved; do not merge publications by VIN.
        uid = ids[0] if len(ids) == 1 else None
        bound_ids = {c.get('unique_id') for c in verified_cards
                     if c.get('unique_id') and c.get('marketing_binding', {}).get('status') == 'matched'
                     and c['unique_id'] not in conflicts
                     and c['marketing_binding'].get('marketing_row') == number}
        if not uid and len(bound_ids) == 1:
            uid = next(iter(bound_ids))
            images = []
            for _, feed in feeds.get(uid, []):
                images += re.findall(r'https://[^\s<>"|]+', feed.get('images') or feed.get('ImageUrls') or '')
        result.append({
            'key': f'marketing:{number}', 'unique_id': uid,
            'identity_conflict_ids': sorted(k for k, c in conflicts.items() if number in c['marketing_rows']),
            'title': ' · '.join(x for x in (row[8], row[9]) if x),
            'vin': row[10], 'price': row[11], 'year': row[12], 'mileage': row[13],
            'owners': row[14], 'responsible': row[4], 'marketing_status': row[5],
            'marketing_row': number, 'image': next((u for u in images if _link(u)), None) if uid else None,
            'expected_urls': {'auto_ru': _link(row[22]), 'avito': _link(row[23])},
            'invalid_urls': {source: str(value) for source, value in [('auto_ru', row[22]), ('avito', row[23])]
                             if str(value).strip() and not (_link(value) and is_marketplace_listing_url(EngineType(source), value))},
            'sheet_url': f'{snapshot.workbook_url}#gid=0&range=F{number}:X{number}',
        })
    return result


def _format_number(value, suffix=''):
    if value is None or value == '':
        return 'Нет данных'
    text = str(value).strip()
    if re.fullmatch(r'\d[\d\s\u00a0\u202f]*', text):
        number = int(''.join(text.split()))
        text = f'{number:,}'.replace(',', ' ')
    return text + suffix


def vehicle_dashboard(report, *, apply_brief=True, photo_index=None, display_bindings=(), display_photos=()):
    content = report.get('content_review') or report.get('automatic_content_checks') or {}
    metadata = content.get('marketing_vehicles', [])
    conflicts = report_identity_conflicts(content)
    groups = {}
    for source_meta in metadata:
        meta = dict(source_meta)
        if content.get('feed_photo_schema_version') == 1:
            # New reports use checked gallery entries; do not silently fall back
            # to an unchecked feed URL when its media check failed.
            meta['image'] = None
        bound_ids = {c.get('unique_id') for c in content.get('cards', [])
                     if c.get('unique_id') and c['unique_id'] not in conflicts
                     and c.get('marketing_binding', {}).get('status') == 'matched'
                     and c['marketing_binding'].get('marketing_row') == meta.get('marketing_row')}
        if len(bound_ids) == 1 and not meta.get('unique_id'):
            # Reuse an already verified publication -> workbook binding, including
            # the explicitly supported intra-workbook AvitoId fallback.
            meta['unique_id'] = next(iter(bound_ids))
        if meta.get('unique_id') in conflicts:
            meta['identity_conflict_ids'] = [meta['unique_id']]
            meta['unique_id'] = None
        meta['identity_conflict'] = bool(meta.get('identity_conflict_ids'))
        key = meta.get('unique_id') or meta['key']
        if key in groups:  # Never choose one of several source rows silently.
            key = meta['key']
        groups[key] = {**meta, 'platforms': [], 'issues': [], 'attention': False, 'incomplete': False}
    content_by_url = {(c['source'], c['url']): c for c in content.get('cards', [])}
    saved_by_url = {(c['source'], c['url']): c for c in report.get('observed_cards', [])}
    for index, saved in enumerate(report.get('observed_cards', [])):
        if saved.get('state') != 'active':
            continue
        reviewed = content_by_url.get((saved['source'], saved['url']), {})
        uid = reviewed.get('unique_id') or saved.get('unique_id')
        conflict = uid in conflicts
        # Explicit, evidence-bound owner review may group a vehicle for display.
        # It never fills the publication's missing unique_id or hides that finding.
        confirmed = None
        if not uid and not saved.get('card', {}).get('placement_id_raw'):
            matches = [b for b in display_bindings
                       if b.get('source') == saved['source']
                       and b.get('url') == saved['url'] and b.get('basis')
                       and b.get('unique_id') not in conflicts
                       and b.get('unique_id') in groups]
            if len(matches) == 1:
                candidate = matches[0]
                target = groups[candidate['unique_id']]
                expected = target.get('expected_urls', {}).get(saved['source'])
                if (expected and canonical_listing_key(EngineType(saved['source']), expected)
                        == canonical_listing_key(EngineType(saved['source']), saved['url'])):
                    confirmed = candidate
            if not confirmed:
                # Display association only: exact saved marketing hyperlink plus
                # the independently retained expected feed ID/row. Not a price,
                # VIN, title or year guess and not publication identity proof.
                linked = [m for m in metadata if m.get('unique_id') in groups
                          and m.get('unique_id') not in conflicts
                          and reviewed.get('expected_workbook_ids') == [m.get('unique_id')]
                          and reviewed.get('expected_marketing_rows') == [m.get('marketing_row')]
                          and m.get('expected_urls', {}).get(saved['source'])
                          and canonical_listing_key(EngineType(saved['source']), m['expected_urls'][saved['source']])
                          == canonical_listing_key(EngineType(saved['source']), saved['url'])]
                if len(linked) == 1:
                    meta = linked[0]
                    confirmed = {'source': saved['source'], 'url': saved['url'],
                                 'unique_id': meta['unique_id'], 'kind': 'saved_marketing_link',
                                 'basis': f"Точная ссылка из строки {meta['marketing_row']} сохранённой таблицы; ожидаемый ID фида {meta['unique_id']}."}
        key = (f"conflict:{saved['source']}:{canonical_listing_key(EngineType(saved['source']), saved['url'])}"
               if conflict else uid or (confirmed or {}).get('unique_id') or f"{saved['source']}:{saved['url']}")
        fields = saved.get('card') or {}
        tile = groups.setdefault(key, {
            'key': key, 'unique_id': uid, 'title': fields.get('title') or reviewed.get('title') or 'Объявление',
            'vin': '', 'price': fields.get('price'), 'year': fields.get('year_attribute') or fields.get('year'),
            'mileage': fields.get('mileage_raw'), 'owners': fields.get('owners_raw'),
            'responsible': '', 'marketing_status': 'Связь с таблицей не подтверждена',
            'image': None, 'expected_urls': {}, 'platforms': [], 'issues': [],
            'attention': False, 'incomplete': False,
            'identity_conflict': conflict,
        })
        if saved.get('evidence'):
            tile['evidence_image'] = f"/api/v1/status/cycles/{report['cycle_id']}/placement-evidence/{index}"
        if confirmed:
            tile.setdefault('display_confirmations', []).append(confirmed)
        if not tile.get('image') and uid and not conflict:
            automatic_photos = [p for p in content.get('feed_photos', [])
                                if p.get('status') == 'verified' and p.get('verification')
                                and safe_feed_photo_url(p.get('image_url'))]
            photos = [p for p in automatic_photos if p.get('unique_id') == uid
                      and p.get('source') == saved['source'] and p.get('url') == saved['url']]
            if not photos:
                photos = [p for p in display_photos if p.get('unique_id') == uid
                          and p.get('source') == saved['source'] and p.get('url') == saved['url']]
            if len(photos) == 1:
                photo = photos[0]
                tile.update(image=photo['image_url'], image_source='Фото из сохранённого фида',
                            image_source_url=photo.get('sheet_url'),
                            image_captured_at=photo.get('captured_at_utc'))
        # Illustration-only lookup by an unambiguous exact marketing URL.
        # The tile remains unbound; neither VIN nor unique_id is copied.
        if not tile.get('image') and not tile.get('marketing_row') and photo_index:
            candidates = [m for m in metadata if m.get('expected_urls', {}).get(saved['source'])
                          and canonical_listing_key(EngineType(saved['source']), m['expected_urls'][saved['source']])
                          == canonical_listing_key(EngineType(saved['source']), saved['url'])]
            if len(candidates) == 1:
                illustration = dict(candidates[0])
                apply_company_photo(illustration, photo_index)
                if illustration.get('image_source'):
                    for field in ('image', 'image_source', 'image_source_url', 'image_captured_at'):
                        tile[field] = illustration.get(field)
        positions = saved.get('supplemental_search_positions') or saved.get('search_positions') or []
        if not reviewed or not positions or not any(p.get('page') is not None or p.get('label') == '5+' for p in positions):
            tile['incomplete'] = True
        tile['platforms'].append({
            'source': saved['source'], 'state': 'active', 'label': 'Активно', 'url': _link(saved.get('url')),
            'position': ' · '.join(dict.fromkeys(p['label'] for p in positions)) or 'Позиция не установлена',
            'price': _format_number(fields.get('price'), ' ₽'),
            'year': fields.get('year_attribute') or fields.get('year') or 'Нет данных',
            'mileage': fields.get('mileage_raw') or _format_number(fields.get('mileage_km'), ' км'),
            'owners': fields.get('owners_raw') or 'Нет данных',
            'captured_at': reviewed.get('captured_at_utc'),
        })
        for f in reviewed.get('findings', []):
            code = f.get('code', '')
            if code in {'identity_visual_alias', 'identity_operator_confirmed', 'feed_description_difference'}:
                continue
            mismatch = code.endswith('_difference') or code.endswith('_conflict')
            tile['attention'] |= mismatch
            tile['incomplete'] |= not mismatch
            tile['issues'].append({**f, 'source': saved['source'], 'is_difference': mismatch,
                                   'cells': reviewed.get('marketing_binding', {}).get('cells', [])})
        if not uid:
            tile['incomplete'] = True
        if conflict:
            tile['incomplete'] = True
            tile['issues'].append({'code': 'identity_conflict', 'title': 'Конфликт unique_id внутри книги',
                                   'detail': 'Публикация сохранена отдельно; строка автомобиля не выбрана.',
                                   'action': 'Проверить противоречащие строки фидов и маркетинга.',
                                   'source': saved['source'], 'is_difference': False, 'cells': []})
    for tile in groups.values():
        for source in ('auto_ru', 'avito'):
            if any(p['source'] == source for p in tile['platforms']):
                continue
            expected = tile.get('expected_urls', {}).get(source)
            full = absence_proven(report, source, expected)
            # A URL match can prevent a false absence, but never assigns a unique_id.
            url_seen = expected and any(
                s == source and canonical_listing_key(EngineType(source), url)
                == canonical_listing_key(EngineType(source), expected)
                and c.get('state') == 'active' for (s, url), c in saved_by_url.items()
            )
            url_unverified = expected and any(
                s == source and canonical_listing_key(EngineType(source), url)
                == canonical_listing_key(EngineType(source), expected)
                and c.get('state') not in {'active', 'removed'} for (s, url), c in saved_by_url.items()
            )
            url_removed = expected and any(
                s == source and canonical_listing_key(EngineType(source), url)
                == canonical_listing_key(EngineType(source), expected)
                and c.get('state') == 'removed' and isinstance(c.get('evidence'), str)
                and c.get('evidence_manifest') == evidence_manifest_name(c['evidence'])
                for (s, url), c in saved_by_url.items()
            )
            invalid = tile.get('invalid_urls', {}).get(source) or (
                expected and not is_marketplace_listing_url(EngineType(source), expected))
            if invalid:
                state, label = 'invalid_link', 'Ошибка ссылки: это не карточка указанной площадки'
                tile['attention'] = True
                expected = None
            elif tile.get('identity_conflict') and expected:
                state, label = 'unknown', 'Не проверено: конфликт unique_id внутри книги'
                tile['incomplete'] = True
            elif url_seen:
                state, label = 'unknown', 'Ссылка активна; связь с таблицей не подтверждена'
                tile['incomplete'] = True
            elif url_unverified:
                state, label = 'unknown', 'Не проверено: состояние карточки не подтверждено'
                tile['incomplete'] = True
            elif expected and not tile.get('unique_id'):
                state, label = 'unknown', 'Связь не подтверждена: ссылка из таблицы не найдена'
                tile['incomplete'] = True
            elif url_removed:
                state, label = 'inactive', 'Неактивно: подтверждено по прямой ссылке'
                tile['attention'] = True
            elif expected and full:
                state, label = 'inactive', 'Неактивно: отсутствует в активном каталоге'
                tile['attention'] = True
            elif expected:
                state, label = 'unknown', 'Не проверено: полнота каталога или проверки карточек не подтверждена'
                tile['incomplete'] = True
            else:
                state, label = 'unplanned', 'В таблице нет ссылки' if tile.get('marketing_row') else 'Размещение не установлено'
            tile['platforms'].append({'source': source, 'state': state, 'label': label, 'url': expected, 'position': ''})
        tile['price_display'] = _format_number(tile.get('price'), ' ₽')
        tile['image'] = _link(tile.get('image'))
        if photo_index:
            apply_company_photo(tile, photo_index)
        tile['sheet_url'] = _link(tile.get('sheet_url'))
        tile['status'] = 'attention' if tile['attention'] else 'incomplete' if tile['incomplete'] else 'ok'
        if all(p['state'] == 'unplanned' for p in tile['platforms']) and tile.get('marketing_row'):
            tile['status'] = 'unplanned'
        tile['search_text'] = ' '.join(str(tile.get(k) or '') for k in ('title', 'unique_id', 'vin', 'responsible')).casefold()
    tiles = sorted(groups.values(), key=lambda t: ({'attention': 0, 'incomplete': 1, 'ok': 2, 'unplanned': 3}[t['status']], t['title']))
    if apply_brief:
        brief = report.get('analytical_summary') or {}
        for tile in tiles:
            uid = tile.get('unique_id')
            tile['placement_exceptions'] = [e for e in brief.get('exceptions', []) if uid and e['unique_id'] == uid]
            for platform in tile['platforms']:
                exception = next((e for e in tile['placement_exceptions'] if e['source'] == platform['source']), None)
                platform['known_exception'] = bool(exception)
                if exception:
                    platform['exception_note'] = exception['detail']
            tile['actionable_inactive'] = any(p['state'] == 'inactive' and not p.get('known_exception') for p in tile['platforms'])
            if (tile['placement_exceptions'] and not tile['actionable_inactive']
                    and not any(p['state'] == 'invalid_link' for p in tile['platforms'])
                    and not any(i['is_difference'] for i in tile['issues'])):
                tile['attention'] = False
                tile['status'] = 'incomplete' if tile['incomplete'] else 'exception'
            for issue in tile['issues']:
                if 'year' not in issue.get('code', ''):
                    continue
                proof = next((d for item in brief.get('items', []) if uid and item.get('unique_id') == uid
                              for d in item.get('confirmations', []) if d['source'] in {'all', issue['source']}), None)
                if proof:
                    issue['confirmation'] = proof
                    issue['action'] = f"Подтверждённый год {proof['value']}: согласовать расходящиеся поля источников и публикации."
        tiles.sort(key=lambda t: ({'attention': 0, 'incomplete': 1, 'ok': 2, 'exception': 3, 'unplanned': 4}[t['status']], t['title']))
    return {'vehicles': tiles, 'attention': sum(t['attention'] for t in tiles),
            'incomplete': sum(t['incomplete'] for t in tiles),
            'inactive': sum(p['state'] == 'inactive' for t in tiles for p in t['platforms']),
            'metadata_available': bool(metadata)}
