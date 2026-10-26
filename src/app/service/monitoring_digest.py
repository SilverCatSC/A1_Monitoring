"""Read-only, audience-oriented presentation of verified monitoring results.

Observed differences are signals, not diagnoses or permission to edit sources.
Unknown/new finding codes remain visible under limitations until classified.
"""

from collections import Counter
from urllib.parse import urlsplit


def _link(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def monitoring_digest(report):
    content = report.get('content_review') or report.get('automatic_content_checks') or {}
    buckets = {name: [] for name in ('placement', 'content', 'quality', 'limitations', 'events', 'notes')}
    for alert in report.get('publication_alerts', {}).get('items', []):
        kind = alert.get('kind')
        confirmed = alert.get('certainty') == 'confirmed'
        row = {
            **alert, 'links': [], 'captured_at': report.get('publication_alert_reviewed_at_utc')
            or report.get('created_at_utc'),
            'owner': 'Маркетинг', 'cells': [],
            'status': 'Сохраняется с предыдущих проверок' if alert.get('ongoing') else 'Выявлено в этом цикле',
            'detail': alert.get('explanation', ''),
        }
        for label, url in [('Прежняя ссылка', alert.get('old_url')), ('Активная ссылка', alert.get('new_url'))]:
            if _link(url):
                row['links'].append({'label': label, 'url': url})
        for url in alert.get('urls', []):
            if _link(url):
                row['links'].append({'label': 'Объявление', 'url': url})
        bucket = 'placement'
        if kind == 'publication_disappeared' and confirmed:
            row['detail'] = 'Объявление отсутствует в полностью прочитанном активном каталоге. Причина не установлена; продажа автомобиля не подтверждена.'
            row['action'] = 'Маркетингу проверить статус размещения и восстановить публикацию, если она должна быть активна. Продажам учитывать отсутствие публичного объявления.'
        elif kind == 'inventory_drop' and confirmed:
            row['detail'] = f"Число публикаций: {alert.get('previous_count')} → {alert.get('observed_count')}. Сравнение с опорной проверкой; это сводка, её нельзя складывать с числом исчезнувших карточек. Причина не установлена."
            row['action'] = 'Маркетингу сверить состав активных публикаций с планом размещения.'
        elif kind in {'publication_restored', 'republished'} and confirmed:
            bucket = 'events'
            row['action'] = 'Учесть восстановление доступности.' if kind == 'publication_restored' else 'Маркетингу использовать новую ссылку; продажи могут направлять её клиентам.'
            row['status'] = 'Изменение подтверждено'
        elif kind in {'publication_inactive', 'duplicate_unique_id'} and confirmed:
            row['action'] = 'Маркетингу проверить статус и корректность публикаций с указанным unique_id.'
        else:
            bucket = 'limitations'
            row['status'] = 'Требует подтверждения'
            row['action'] = 'Не считать отсутствие или причину установленными. Уточнить только недостающие данные проверки.'
        buckets[bucket].append(row)

    grouped = {}
    for card in content.get('cards', []):
        for finding in card.get('findings', []):
            code = finding.get('code', '')
            # Text/ID rendering differences are not automatically business errors.
            if code in {'feed_description_difference', 'identity_visual_alias', 'identity_operator_confirmed'}:
                bucket, status = 'notes', 'Справочно; ошибка не подтверждена'
            elif code in {'generic_description', 'marketing_equipment_missing', 'marketing_vin_empty', 'powertrain_as_equipment'}:
                bucket, status = 'quality', 'Требует проверки качества данных'
            elif code.endswith('_difference') or code.endswith('_conflict'):
                bucket, status = 'content', 'Обнаружено расхождение; верное значение требует подтверждения'
            else:
                bucket, status = 'limitations', 'Недостаточно данных для вывода'
            key = (bucket, code)
            if key not in grouped:
                group = {'title': finding.get('title', code), 'code': code, 'cards': []}
                grouped[key] = group
                buckets[bucket].append(group)
            action = finding.get('action', '')
            owner = 'Маркетинг'
            if bucket == 'content':
                owner = 'Маркетинг; продажи подтверждают характеристики и условия продажи'
                action = 'Продажам подтвердить актуальное значение. Маркетингу согласовать данные таблицы, фида и публикации. ' + action
            elif code == 'marketing_identity_unresolved':
                action = 'Маркетингу обеспечить unique_id в публикации и однозначную связь строк таблицы. До этого сверка не завершена; ID по цене или VIN объявления не назначается.'
            elif bucket == 'quality':
                action = 'Маркетингу проверить и дополнить данные; при необходимости запросить сведения у продаж. ' + action
            elif bucket == 'limitations':
                owner = 'Сопровождение мониторинга; маркетинг — при нехватке исходных данных'
            elif bucket == 'notes':
                owner = 'Справочно'
            grouped[key]['cards'].append({
                'source': card.get('source'), 'unique_id': card.get('unique_id'),
                'title': card.get('title'), 'detail': finding.get('detail', ''),
                'action': action, 'owner': owner, 'status': status,
                'captured_at': card.get('captured_at_utc'),
                'links': [{'label': 'Объявление', 'url': card['url']}] if _link(card.get('url')) else [],
                'cells': [ref for ref in card.get('marketing_binding', {}).get('cells', []) if _link(ref.get('url'))],
            })
    observed = report.get('observed_cards', [])
    counts = Counter(c.get('source') for c in observed if c.get('state') == 'active')
    positions = Counter()
    for card in observed:
        if card.get('state') != 'active':
            continue
        saved = card.get('supplemental_search_positions') or card.get('search_positions') or []
        positions['found' if any(p.get('page') is not None for p in saved) else
                  'outside' if any(p.get('label') == '5+' for p in saved) else 'unknown'] += 1
    return {
        **buckets, 'active_counts': counts, 'positions': positions,
        'cards_with_content': len(content.get('cards', [])),
        'confirmed_ids': sum(bool(c.get('unique_id')) for c in content.get('cards', [])),
        'content_available': bool(content),
        'created_at': report.get('created_at_utc'),
        'marketing_read_at': content.get('marketing_comparison', {}).get('read_finished_at_utc'),
    }
