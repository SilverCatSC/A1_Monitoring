"""Detect contradictory UID claims inside workbook feeds before joining adverts."""

from collections import Counter, defaultdict

from app.service.vehicle_content import vin_values


def workbook_identity_conflicts(snapshot):
    claims = defaultdict(list)
    main = [(n, vin_values(row[10])) for n, row in enumerate(snapshot.marketing_rows[1:], 2)
            if len(row) > 10 and row[5].strip().casefold() == 'актуально']
    for sheet, rows in snapshot.rows_by_sheet.items():
        source = 'auto_ru' if sheet == 'autoru-feed-all' else 'avito'
        for number, row in enumerate(rows, snapshot.first_data_rows.get(sheet, 1)):
            uid = (row.get('unique_id') or row.get('Id') or '').strip()
            if uid:
                claims[uid].append((source, vin_values(row.get('vin') or row.get('VIN')), sheet, number))
    conflicts = {}
    for uid, entries in claims.items():
        known = [vins for _, vins, _, _ in entries if vins]
        mapped_rows = {n for n, vins in main if any(vins & value for value in known)}
        common_documented_row = any(all(vins & value for value in known) for _, vins in main)
        disjoint = len(known) > 1 and not set.intersection(*(set(v) for v in known))
        duplicate = any(n > 1 for n in Counter(source for source, *_ in entries).values())
        if len(mapped_rows) > 1 or (disjoint and not common_documented_row) or duplicate:
            conflicts[uid] = {'unique_id': uid, 'code': 'workbook_identity_conflict',
                              'marketing_rows': sorted(mapped_rows),
                              'feed_rows': [{'sheet': sheet, 'row': number} for _, _, sheet, number in entries]}
    return conflicts


def report_identity_conflicts(content):
    """Protect legacy reports too: never choose the first of conflicting bindings."""
    mapped = defaultdict(set)
    conflicts = set(content.get('identity_conflicts', {}))
    for meta in content.get('marketing_vehicles', []):
        if meta.get('unique_id') and meta.get('marketing_row') is not None:
            mapped[meta['unique_id']].add(meta['marketing_row'])
        conflicts.update(meta.get('identity_conflict_ids', []))
    for card in content.get('cards', []):
        uid, binding = card.get('unique_id'), card.get('marketing_binding') or {}
        if uid and binding.get('status') == 'matched' and binding.get('marketing_row') is not None:
            mapped[uid].add(binding['marketing_row'])
        if uid and binding.get('status') == 'workbook_identity_conflict':
            conflicts.add(uid)
    return conflicts | {uid for uid, rows in mapped.items() if len(rows) > 1}
