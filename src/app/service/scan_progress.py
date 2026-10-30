from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.service.completion import summarize_cycle_completion

PROGRESS_FILENAME = 'scan_progress.json'


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class ScanProgressTracker:
    """Persist and print a small live view of the local browser scan."""

    def __init__(self, evidence_dir: str) -> None:
        self.path = Path(evidence_dir) / PROGRESS_FILENAME
        self.state: dict[str, Any] = {
            'status': 'starting',
            'started_at': _timestamp(),
            'updated_at': _timestamp(),
            'total_filters': 0,
            'completed_filters': 0,
            'events': [],
        }

    def __call__(self, payload: dict[str, Any]) -> None:
        event = {**payload, 'at': _timestamp()}
        name = str(event.get('event') or '')
        if name == 'cycle_registered':
            self.state['cycle_id'] = event.get('cycle_id')
        elif name == 'cycle_started':
            self.state.update(
                status='running',
                started_at=event['at'],
                total_filters=int(event.get('total_filters') or 0),
                completed_filters=0,
                summary=None,
                error=None,
            )
        elif name == 'source_refresh_started':
            self.state.update(status='preparing', error=None, started_at=event['at'],
                              total_filters=0, completed_filters=0, summary=None, current={}, events=[], preflight=None,
                              publication_alerts=[])
        elif name == 'publication_alert':
            alerts = list(self.state.get('publication_alerts') or [])
            alerts.append(event)
            self.state['publication_alerts'] = alerts[-500:]
        elif name == 'source_refresh_failed':
            self.state.update(status='failed', error=event.get('error'))
        elif name == 'source_recovery_waiting':
            if self.state.get('status') != 'waiting_source':
                self.state['before_source_status'] = self.state.get('status', 'preparing')
            self.state.update(status='waiting_source', source_recovery=event)
        elif name == 'source_recovery_resolved':
            self.state['status'] = self.state.pop('before_source_status', 'preparing')
            self.state['source_recovery'] = {**event, 'resolved': True}
        elif name == 'search_collection_started':
            self.state.update(status='running', current={}, completed_filters=0,
                              total_filters=int(event.get('total_filters') or 0))
        elif name == 'dealer_preflight_started':
            self.state.update(status='reconciling', current={}, preflight=None)
        elif name == 'dealer_preflight_finished':
            self.state['preflight'] = event.get('summary')
        elif name == 'link_preflight_started':
            self.state.update(status='link_sync', current={})
        elif name == 'link_sync_finished':
            self.state['link_sync'] = {
                'status': event.get('status'), 'updated': event.get('updated'),
                'blocked': event.get('blocked'),
            }
        elif name == 'direct_cards_started':
            self.state['status'] = 'checking_cards'
            self.state['direct_cards'] = {'total': event.get('total'), 'checked': 0}
        elif name == 'direct_cards_finished':
            self.state['direct_cards'] = event.get('summary')
            self.state['status'] = 'checking_cards'
        elif name == 'direct_card_finished':
            direct_cards = dict(self.state.get('direct_cards') or {})
            direct_cards['checked'] = int(event.get('card_index') or 0)
            direct_cards['total'] = int(event.get('card_total') or direct_cards.get('total') or 0)
            self.state['direct_cards'] = direct_cards
        elif name == 'navigation_operator_required':
            if self.state.get('status') != 'waiting_navigation':
                self.state['before_navigation_status'] = self.state.get('status', 'running')
            self.state['status'] = 'waiting_navigation'
        elif name == 'navigation_operator_waiting':
            self.state['status'] = 'waiting_navigation'
        elif name == 'navigation_operator_resolved':
            self.state['status'] = self.state.pop('before_navigation_status', 'running')
        elif name == 'captcha_operator_required':
            self.state['before_captcha_status'] = self.state.get('status', 'running')
            self.state['status'] = 'waiting_captcha'
        elif name == 'captcha_operator_waiting':
            self.state['status'] = 'waiting_captcha'
        elif name in {'captcha_operator_resolved', 'captcha_operator_unresolved'}:
            self.state['status'] = self.state.pop('before_captcha_status', 'running')
        elif name in {'filter_finished', 'filter_skipped'}:
            self.state['completed_filters'] = int(event.get('overall_index') or 0)
        elif name == 'cycle_finished':
            summary = event.get('summary') or {}
            self.state['summary'] = summary
            self.state['status'] = 'checking_cards'
        elif name == 'cycle_completed':
            search_summary = self.state.get('summary') or {}
            direct_cards = self.state.get('direct_cards') or {}
            summary = event.get('summary') or summarize_cycle_completion(search_summary, direct_cards)
            self.state['status'] = summary.get('status', 'partial')
            self.state['summary'] = summary
            self.state['completed_filters'] = self.state.get('total_filters', 0)
        elif name == 'cycle_failed':
            self.state['status'] = 'failed'
            self.state['error'] = event.get('error')

        if name in {
            'source_started',
            'source_refresh_started',
            'source_refresh_finished',
            'source_refresh_failed',
            'filter_wait',
            'filter_retry',
            'filter_started',
            'page_wait',
            'page_started',
            'page_finished',
            'page_failed',
            'navigation_operator_required',
            'navigation_operator_waiting',
            'navigation_operator_resolved',
            'lazy_load_started',
            'lazy_load_finished',
            'captcha_refresh',
            'captcha_operator_required',
            'captcha_operator_waiting',
            'captcha_manual_refresh_hint',
            'captcha_operator_resolved',
            'captcha_operator_unresolved',
            'filter_finished',
            'filter_skipped',
            'source_finished',
            'dealer_catalogue_started',
            'dealer_catalogue_finished',
            'dealer_link_check',
            'direct_content_waiting',
            'direct_navigation_waiting',
            'direct_cards_started',
            'direct_card_finished',
            'direct_cards_finished',
        }:
            current = dict(self.state.get('current') or {})
            current.update({key: value for key, value in event.items() if key != 'event'})
            current['event'] = name
            self.state['current'] = current

        self.state['updated_at'] = event['at']
        events = list(self.state.get('events') or [])
        events.append(event)
        self.state['events'] = events[-80:]
        self._write()
        print(_terminal_line(event), flush=True)

    def fail(self, error: str) -> None:
        self({'event': 'cycle_failed', 'error': error})

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        temporary.replace(self.path)


def read_scan_progress(evidence_dir: str) -> dict[str, Any]:
    path = Path(evidence_dir) / PROGRESS_FILENAME
    if not path.is_file():
        return {'status': 'idle', 'events': []}
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError('progress file is too large')
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('progress payload is not an object')
        return payload
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {'status': 'unavailable', 'events': [], 'error': str(exc)}


def _terminal_line(event: dict[str, Any]) -> str:
    stamp = datetime.fromisoformat(event['at']).astimezone().strftime('%H:%M:%S')
    source = {'auto_ru': 'Auto.ru', 'avito': 'Avito'}.get(
        str(event.get('source') or ''), str(event.get('source') or '')
    )
    name = str(event.get('event') or '')
    if name == 'source_recovery_waiting':
        return (f'[{stamp}] GOOGLE SHEETS · временная ошибка загрузки; повтор через '
                f'{event["retry_seconds"]} с. Проверка ожидает; старые данные не используются.')
    if name == 'source_recovery_resolved':
        return f'[{stamp}] GOOGLE SHEETS · доступ восстановлен; продолжаем текущую проверку'
    if name == 'publication_alert':
        detail = (f" · было {event['previous_count']}, обнаружено {event['observed_count']}"
                  if event.get('kind') == 'inventory_drop' else
                  f" · unique_id {event['unique_id']}" if event.get('unique_id') else '')
        uncertain = ' · полнота/состояние не подтверждены' if event.get('certainty') != 'confirmed' else ''
        return f'[{stamp}] СИГНАЛ · {source} · {event.get("title")}{detail}{uncertain}'
    if name == 'content_review_finished':
        return (f'[{stamp}] СОДЕРЖАНИЕ · проверено карточек {event.get("cards_checked", 0)} · '
                f'с предупреждениями {event.get("cards_with_warnings", 0)} · сверка с таблицей отдельно')
    if name == 'search_collection_started':
        return f'[{stamp}] ПОИСК · сначала собираем выдачу, до посещения карточек'
    if name == 'search_collection_finished':
        return f'[{stamp}] ПОИСК · выдача сохранена; переходим к активному каталогу и карточкам'
    if name == 'dealer_preflight_started':
        return f'[{stamp}] СВЕРКА · проверяем актуальные ссылки после сбора выдачи'
    if name == 'dealer_catalogue_started':
        return f'[{stamp}] СВЕРКА · {source} · {event["url"]} · пауза {event["wait_seconds"]} с'
    if name == 'dealer_catalogue_finished':
        return f'[{stamp}] СВЕРКА · {source} · кандидатов {event["candidates"]} · весь каталог: {event["complete"]} · {event.get("error") or ""}'
    if name in {'direct_content_waiting', 'direct_navigation_waiting'}:
        return (f'[{stamp}] КАРТОЧКА · {source} · ждём содержимое · '
                f'{event.get("url")} · объявление не пропускается')
    if name == 'dealer_link_check':
        label = 'КАРТОЧКА' if event.get('purpose') == 'card_detail' else 'СВЕРКА · старая ссылка'
        position = (
            f' {event.get("card_index")}/{event.get("card_total")}'
            if event.get('card_index') and event.get('card_total')
            else ''
        )
        return f'[{stamp}] {label}{position} · {event["url"]} · пауза {event["wait_seconds"]} с'
    if name == 'dealer_preflight_finished':
        return f'[{stamp}] СВЕРКА · итог {event["summary"]} · спорные ссылки не считаются непоказами'
    if name == 'direct_cards_started':
        return f'[{stamp}] КАРТОЧКИ · проверяем прямые ссылки после поиска · всего: {event.get("total", 0)}'
    if name == 'direct_cards_finished':
        return f'[{stamp}] КАРТОЧКИ · итог {event.get("summary") or {}}'
    if name == 'direct_card_finished':
        return (
            f'[{stamp}] КАРТОЧКА {event.get("card_index")}/{event.get("card_total")} · '
            f'{source} · {event.get("vehicle") or "автомобиль"} · '
            f'{event.get("status_code") or "unknown"}'
        )
    if name == 'cycle_started':
        return f'[{stamp}] СТАРТ · фильтров: {event.get("total_filters", 0)}'
    if name == 'source_refresh_started':
        return f'[{stamp}] РЕЕСТР · обновление Monitoring из Google Sheets…'
    if name == 'source_refresh_finished':
        return (
            f'[{stamp}] РЕЕСТР · обновлён · валидных: {event.get("rows_valid", 0)} '
            f'из {event.get("rows_total", 0)}'
        )
    if name == 'source_refresh_failed':
        return f'[{stamp}] РЕЕСТР · ОШИБКА: {event.get("error")}'
    if name == 'source_started':
        return f'[{stamp}] {source} · начало · фильтров: {event.get("filters_total", 0)}'
    if name == 'filter_wait':
        return (
            f'[{stamp}] {source} · щадящая пауза {event.get("wait_seconds")} с · '
            f'перед фильтром {event.get("filter_name")}'
        )
    if name == 'filter_retry':
        return (
            f'[{stamp}] {source} · вкладка Chrome была закрыта · '
            f'повтор фильтра через {event.get("retry_seconds")} с'
        )
    if name == 'filter_started':
        if event.get('stage') == 'matching_saved_search':
            return f'[{stamp}] {source} · сопоставляем сохранённую выдачу · {event.get("filter_name")}'
        return (
            f'[{stamp}] {source} · фильтр {event.get("source_index")}/'
            f'{event.get("source_total")} · {event.get("filter_name")} · '
            f'ожидаемых авто: {event.get("expected", 0)}'
        )
    if name == 'page_started':
        return (
            f'[{stamp}] {source} · страница {event.get("page")}/'
            f'{event.get("pages_total")} · загрузка…'
        )
    if name == 'page_wait':
        return (
            f'[{stamp}] {source} · щадящая пауза {event.get("wait_seconds")} с · '
            f'перед страницей {event.get("page")}/{event.get("pages_total")}'
        )
    if name == 'page_finished':
        return (
            f'[{stamp}] {source} · страница {event.get("page")}/'
            f'{event.get("pages_total")} · карточек: {event.get("cards", 0)} · '
            f'целевых снимков: {event.get("target_cards", 0)} · {event.get("state")}'
        )
    if name == 'page_failed':
        return f'[{stamp}] {source} · страница {event.get("page")} · ОШИБКА: {event.get("error")}'
    if name == 'lazy_load_started':
        return f'[{stamp}] {source} · подгрузка в текущей вкладке · пауза {event.get("wait_seconds")} с'
    if name == 'lazy_load_finished':
        return f'[{stamp}] {source} · подгрузка завершена · собрано карточек: {event.get("cards")}'
    if name in {'navigation_operator_required', 'navigation_operator_waiting'}:
        return (f'[{stamp}] {source} · страница {event.get("page")} не загружена; '
                'цикл ждёт восстановления доступа. Не закрывайте вкладку; '
                'после восстановления обновите ЭТУ ЖЕ вкладку (Cmd+R).')
    if name == 'navigation_operator_resolved':
        return f'[{stamp}] {source} · доступ восстановлен; продолжаем ту же страницу'
    if name == 'captcha_refresh':
        if event.get('reason') == 'ip_restriction':
            return (f'[{stamp}] {source} · ограничение IP · обновление '
                    f'{event.get("attempt")}/{event.get("limit")} в той же вкладке')
        return f'[{stamp}] {source} · CAPTCHA · одно обычное обновление страницы'
    if name == 'captcha_operator_required':
        timing = ('без ограничения времени; остановка — Ctrl+C' if event.get('wait_seconds') == -1
                  else f'в течение {event.get("wait_seconds")} с')
        return f'[{stamp}] {source} · пройдите проверку в открытом Chrome · {timing}'
    if name == 'captcha_operator_resolved':
        if event.get('outcome') == 'cleared_by_refresh':
            return f'[{stamp}] {source} · ограничение IP снято после обновления · продолжаем текущую страницу'
        return f'[{stamp}] {source} · CAPTCHA пройдена · продолжаем текущую страницу'
    if name == 'captcha_operator_waiting':
        reason = {
            'ip_refresh_wait': 'сообщение об ограничении IP; ждём перед обновлением этой страницы',
            'ip_refresh_exhausted': 'повторы исчерпаны; ждём в той же вкладке, объявление не пропускается',
            'profile_load_failed': 'Avito не загрузил профиль; нажмите «Обновить» в этой вкладке — проверка ждёт',
            'redirect_pending': 'проверка пройдена; ждём перехода Avito, вкладку не обновляйте',
            'redirect_stalled': 'Avito не завершил переход после проверки; обновите ЭТУ ЖЕ вкладку один раз (Cmd+R)',
            'challenge_present': 'страница проверки ещё открыта',
            'stabilizing': 'проверяем восстановленную страницу',
            'wrong_destination': 'открыт другой адрес; вернитесь к исходной странице',
            'manual_refresh_required': 'проверка снята, но документ ещё отдаёт отказ; обновите ЭТУ ЖЕ вкладку (Cmd+R)',
            'awaiting_successful_document': 'ждём HTTP 200 страницы; если каталог уже открыт, обновите его вручную',
        }.get(event.get('wait_reason'), 'ждём прохождения проверки')
        return f'[{stamp}] {source} · ожидание CAPTCHA · {reason} · HTTP {event.get("http_status")}'
    if name == 'captcha_manual_refresh_hint':
        if event.get('instruction'):
            return f'[{stamp}] {source} · {event["instruction"]}'
        return f'[{stamp}] {source} · КАТАЛОГ УЖЕ ОТКРЫТ? — обновите ЭТУ ЖЕ вкладку (Cmd+R), чтобы программа получила HTTP 200'
    if name == 'captcha_operator_unresolved':
        return f'[{stamp}] {source} · CAPTCHA не снята · проверка остаётся незавершённой'
    if name == 'filter_finished':
        if event.get('collection_only'):
            return f'[{stamp}] {source} · выдача сохранена · {event.get("filter_name")} · карточек: {event.get("cards", 0)} · {event.get("status")}'
        return (
            f'[{stamp}] {source} · {event.get("filter_name")} · {event.get("status")} · '
            f'найдено своих: {event.get("found", 0)}/{event.get("expected", 0)}'
            + (f' · ссылок пропущено: {event["links_rejected"]}' if event.get('links_rejected') else '')
        )
    if name == 'filter_skipped':
        return f'[{stamp}] {source} · {event.get("filter_name")} · ПРОПУЩЕН: {event.get("reason")}'
    if name == 'source_finished':
        return f'[{stamp}] {source} · завершено'
    if name == 'cycle_finished':
        return f'[{stamp}] ПОИСК ЗАВЕРШЁН · переходим к прямым карточкам'
    if name == 'cycle_completed':
        return f'[{stamp}] ФИНИШ ЦИКЛА · {json.dumps(event.get("summary") or {}, ensure_ascii=False)}'
    if name == 'cycle_failed':
        return f'[{stamp}] СБОЙ ЦИКЛА · {event.get("error")}'
    return f'[{stamp}] {name}'
