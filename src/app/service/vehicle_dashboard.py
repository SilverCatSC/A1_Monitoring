"""Vehicle tiles from saved evidence; VIN joins workbook rows, never adverts."""

import re
from collections import defaultdict

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.service.monitoring_digest import _link
from app.service.vehicle_content import vin_values

MAIN = 'Размещение_автомобилей'


def marketing_vehicle_snapshot(snapshot, verified_cards=()):
    """Small display projection retained with the content report for future cycles."""
    if not snapshot.marketing_sha256:
        return []
    feeds = defaultdict(list)
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
                     and c['marketing_binding'].get('marketing_row') == number}
        if not uid and len(bound_ids) == 1:
            uid = next(iter(bound_ids))
            images = []
            for _, feed in feeds.get(uid, []):
                images += re.findall(r'https://[^\s<>"|]+', feed.get('images') or feed.get('ImageUrls') or '')
        result.append({
            'key': f'marketing:{number}', 'unique_id': uid,
            'title': ' · '.join(x for x in (row[8], row[9]) if x),
            'vin': row[10], 'price': row[11], 'year': row[12], 'mileage': row[13],
            'owners': row[14], 'responsible': row[4], 'marketing_status': row[5],
            'marketing_row': number, 'image': next((u for u in images if _link(u)), None) if uid else None,
            'expected_urls': {'auto_ru': _link(row[22]), 'avito': _link(row[23])},
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


def vehicle_dashboard(report):
    content = report.get('content_review') or report.get('automatic_content_checks') or {}
    metadata = content.get('marketing_vehicles', [])
    groups = {}
    for source_meta in metadata:
        meta = dict(source_meta)
        bound_ids = {c.get('unique_id') for c in content.get('cards', [])
                     if c.get('unique_id') and c.get('marketing_binding', {}).get('status') == 'matched'
                     and c['marketing_binding'].get('marketing_row') == meta.get('marketing_row')}
        if len(bound_ids) == 1 and not meta.get('unique_id'):
            # Reuse an already verified publication -> workbook binding, including
            # the explicitly supported intra-workbook AvitoId fallback.
            meta['unique_id'] = next(iter(bound_ids))
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
        key = uid or f"{saved['source']}:{saved['url']}"
        fields = saved.get('card') or {}
        tile = groups.setdefault(key, {
            'key': key, 'unique_id': uid, 'title': fields.get('title') or reviewed.get('title') or 'Объявление',
            'vin': '', 'price': fields.get('price'), 'year': fields.get('year_attribute') or fields.get('year'),
            'mileage': fields.get('mileage_raw'), 'owners': fields.get('owners_raw'),
            'responsible': '', 'marketing_status': 'Связь с таблицей не подтверждена',
            'image': None, 'expected_urls': {}, 'platforms': [], 'issues': [],
            'attention': False, 'incomplete': False,
        })
        if not tile.get('image') and saved.get('evidence'):
            tile['evidence_image'] = f"/api/v1/status/cycles/{report['cycle_id']}/placement-evidence/{index}"
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
    for tile in groups.values():
        for source in ('auto_ru', 'avito'):
            if any(p['source'] == source for p in tile['platforms']):
                continue
            expected = tile.get('expected_urls', {}).get(source)
            full = report.get('catalogue_enumeration_complete', {}).get(source) is True
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
            if url_seen:
                state, label = 'unknown', 'Ссылка активна; связь с таблицей не подтверждена'
                tile['incomplete'] = True
            elif url_unverified:
                state, label = 'unknown', 'Не проверено: состояние карточки не подтверждено'
                tile['incomplete'] = True
            elif expected and full:
                state, label = 'inactive', 'Неактивно: отсутствует в активном каталоге'
                tile['attention'] = True
            elif expected:
                state, label = 'unknown', 'Не проверено: каталог прочитан не полностью'
                tile['incomplete'] = True
            else:
                state, label = 'unplanned', 'В таблице нет ссылки' if tile.get('marketing_row') else 'Размещение не установлено'
            tile['platforms'].append({'source': source, 'state': state, 'label': label, 'url': expected, 'position': ''})
        tile['price_display'] = _format_number(tile.get('price'), ' ₽')
        tile['image'] = _link(tile.get('image'))
        tile['sheet_url'] = _link(tile.get('sheet_url'))
        tile['status'] = 'attention' if tile['attention'] else 'incomplete' if tile['incomplete'] else 'ok'
        if all(p['state'] == 'unplanned' for p in tile['platforms']) and tile.get('marketing_row'):
            tile['status'] = 'unplanned'
        tile['search_text'] = ' '.join(str(tile.get(k) or '') for k in ('title', 'unique_id', 'vin', 'responsible')).casefold()
    tiles = sorted(groups.values(), key=lambda t: ({'attention': 0, 'incomplete': 1, 'ok': 2, 'unplanned': 3}[t['status']], t['title']))
    return {'vehicles': tiles, 'attention': sum(t['attention'] for t in tiles),
            'incomplete': sum(t['incomplete'] for t in tiles),
            'inactive': sum(p['state'] == 'inactive' for t in tiles for p in t['platforms']),
            'metadata_available': bool(metadata)}
