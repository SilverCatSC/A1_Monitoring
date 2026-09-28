#!/usr/bin/env python3
"""Render one saved no-VPN cycle as a private, read-only local HTML summary."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def _cell(value: object) -> str:
    return html.escape(str(value if value is not None else '—'))


def build_report(cycle: dict, scans: dict) -> str:
    cycle_id = cycle.get('id')
    if not cycle_id or scans.get('cycle_id') != cycle_id:
        raise ValueError('cycle status and scan status must describe the same cycle')
    if cycle.get('network_profile') != 'local_no_vpn':
        raise ValueError('only no-VPN diagnostic cycles may use this report')

    summary = cycle.get('summary') or {}
    pending = (summary.get('pending_checks') or {}).get('count', 0)
    rows = []
    for run in scans.get('runs') or []:
        counts = run.get('state_counts') or {}
        rows.append(
            '<tr>'
            f'<td>{_cell("Auto.ru" if run.get("source") == "auto_ru" else "Avito")}</td>'
            f'<td>{_cell(run.get("status"))}</td>'
            f'<td>{_cell(run.get("filters_ok"))} / {_cell(run.get("filters_total"))}</td>'
            f'<td>{_cell(run.get("pages_scanned"))}</td>'
            f'<td>{_cell(counts.get("found", 0))}</td>'
            f'<td>{_cell(counts.get("review_required", 0))}</td>'
            f'<td>{_cell(counts.get("technical_error", 0))}</td>'
            '</tr>'
        )
    reasons = ', '.join(summary.get('partial_reasons') or []) or '—'
    status_url = f'http://127.0.0.1:18000/api/v1/status/cycles/{html.escape(cycle_id)}'
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Диагностика без VPN</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#17212b}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd3db;padding:10px;text-align:left}}
th{{background:#edf3f8}}.notice{{background:#fff3d7;padding:16px;border-radius:8px}}
a{{color:#075cab}}code{{overflow-wrap:anywhere}}</style></head><body>
<h1>Проверка Auto.ru и Avito без VPN</h1>
<p class="notice">Это локальный диагностический цикл, не production-приёмка.
Техническая блокировка или CAPTCHA не означает отсутствия объявления.</p>
<p>Цикл: <code>{_cell(cycle_id)}</code><br>
Начало: {_cell(cycle.get('started_at'))}<br>
Завершение: {_cell(cycle.get('finished_at'))}<br>
Итог: <strong>{_cell(cycle.get('status'))}</strong><br>
Ожидают разбора: {_cell(pending)}<br>
Причины неполноты: {_cell(reasons)}</p>
<table><thead><tr><th>Площадка</th><th>Статус</th><th>Фильтры</th>
<th>Страницы</th><th>Найдено</th><th>Требует разбора</th><th>Тех. ошибки поиска</th>
</tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7">Нет поисковых запусков</td></tr>'}</tbody></table>
<p><a href="{status_url}">Полный статус этого цикла</a> ·
<a href="http://127.0.0.1:18000/api/v1/dashboard/listings">Список автомобилей</a> ·
<a href="host_runner.log">Журнал запуска</a> ·
<a href="scan_status.json">Подробности по площадкам</a></p>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    directory = args.run_dir
    cycle = json.loads((directory / 'cycle_status.json').read_text(encoding='utf-8'))
    scans = json.loads((directory / 'scan_status.json').read_text(encoding='utf-8'))
    (directory / 'result.html').write_text(build_report(cycle, scans), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
