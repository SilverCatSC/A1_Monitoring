"""Build an explicitly public, read-only dashboard snapshot; never deploy implicitly.

This is separate from export_public_report.py's aggregate-only contract.
The caller must explicitly accept publication of vehicle details and findings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from bs4 import BeautifulSoup, Comment

ROOT = Path(__file__).resolve().parents[1]
LINK_HOSTS = {'auto.ru', 'www.auto.ru', 'avito.ru', 'www.avito.ru', 'a1auto.ru', 'www.a1auto.ru'}
SECTIONS = ('#business-review', '.monitor-tabs', '#vehicle-panel', '#marketing-panel', '#publication-alerts')
CSS = ('app.css', 'navigation-responsive.css', 'monitoring.css', 'monitoring-theme.css')


def public_url(value: str, *, image: bool = False) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port:
        return False
    if image:
        return ((parsed.hostname == 'storage.yandexcloud.net' and parsed.path.startswith('/a1autofeed/'))
                or (parsed.hostname in {'a1auto.ru', 'www.a1auto.ru'} and parsed.path.startswith('/upload/')))
    return parsed.hostname in LINK_HOSTS


def scan_time(html: str) -> str:
    source = BeautifulSoup(html, 'html.parser')
    time = source.select_one('#business-review [data-monitor-time]')
    value = time.get('data-monitor-time', '') if time else ''
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Scan time must include a timezone')
    return parsed.astimezone(UTC).isoformat()


def project_html(html: str, *, checks: list[str] | None = None) -> tuple[str, str]:
    source = BeautifulSoup(html, 'html.parser')
    report_link = source.select_one('a[href$="/placement-report"]')
    match = re.fullmatch(r'/api/v1/status/cycles/([0-9a-f-]{36})/placement-report',
                         report_link.get('href', '') if report_link else '')
    if not match or any(source.select_one(selector) is None for selector in SECTIONS):
        raise ValueError('A complete rendered dashboard with a cycle identifier is required')
    cycle_id = match[1]
    page = BeautifulSoup('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                         '<meta name="viewport" content="width=device-width,initial-scale=1">'
                         '<meta name="robots" content="noindex,nofollow">'
                         '<meta name="referrer" content="no-referrer">'
                         '<title>Мониторинг автомобилей · А1 АВТО</title></head>'
                         '<body class="workbench monitoring-workspace shared-report">'
                         '<main class="app-content" id="main-content"></main></body></html>', 'html.parser')
    for name in (*CSS, 'shared.css'):
        page.head.append(page.new_tag('link', rel='stylesheet', href=f'static/{name}'))
    header = BeautifulSoup('<header class="workspace-bar"><div class="workspace-identity">'
                           '<span class="workspace-mark">A1</span><span>Мониторинг объявлений'
                           '<small>Общедоступный отчёт · только чтение</small></span></div>'
                           '<nav class="workspace-navigation" aria-label="Отчёт">'
                           '<a href="#vehicle-panel">Автомобили</a>'
                           '<a href="#marketing-summary">Маркетингу</a><a href="history.html">История</a></nav></header>'
                           '<p class="monitor-note">Сохранённый снимок последней опубликованной проверки. '
                           'Он не обновляется в реальном времени. Даты наблюдений и ограничения указаны ниже. '
                           'Непроверенное объявление не считается неактивным.</p>', 'html.parser')
    page.main.extend(list(header.contents))
    history_link = page.new_tag('p', attrs={'class': 'shared-history-entry'})
    action = page.new_tag('a', href='history.html')
    action.string = 'История проверок и дни активности →'
    history_link.append(action)
    page.main.append(history_link)
    timestamp = source.select_one('#business-review [data-monitor-time]')
    if timestamp:
        status = page.new_tag('section', attrs={'class': 'coverage-status', 'id': 'publication-status',
                                               'role': 'note', 'aria-label': 'Дата и полнота проверки'})
        label = page.new_tag('p')
        label.string = 'Дата проверки (UTC): ' + scan_time(html).replace('T', ' ').replace('+00:00', '')
        status.append(label)
        for text in checks or []:
            item = page.new_tag('p')
            item.string = text
            status.append(item)
        page.main.append(status)
    for selector in SECTIONS:
        page.main.append(source.select_one(selector).extract())
    # No raw evidence, workbook links/cell coordinates, internal JSON or operational controls.
    for node in list(page.select('script, form, iframe, object, embed, base, .monitor-evidence-link')):
        node.decompose()
    for details in list(page.select('details')):
        summary = details.find('summary', recursive=False)
        if summary and summary.get_text(strip=True) == 'Ячейки источника':
            details.decompose()
    for anchor in list(page.select('a[href]')):
        href = anchor['href']
        if href == 'history.html':
            pass
        elif href.startswith('/api/v1/dashboard/monitoring-history#vehicle-') and re.fullmatch(r'[a-f0-9]{20}', href.rsplit('vehicle-', 1)[-1]):
            anchor['href'] = 'history.html#vehicle-' + href.rsplit('vehicle-', 1)[-1]
        elif href == '#monitoring-evidence':
            anchor['href'] = '#business-review'
        elif not href.startswith('#') and not public_url(href):
            # Remove whole link, including workbook row/cell labels.
            anchor.decompose()
        elif not href.startswith('#'):
            anchor['rel'] = 'noopener noreferrer'
            anchor['referrerpolicy'] = 'no-referrer'
    for img in list(page.select('img')):
        if not public_url(img.get('src', ''), image=True):
            img.decompose()
        else:
            img['referrerpolicy'] = 'no-referrer'
    for node in page.find_all(True):
        for attr in list(node.attrs):
            if attr.startswith('on') or attr in {'srcset', 'formaction', 'action'}:
                del node[attr]
    for comment in page.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    footer = page.new_tag('footer', attrs={'class': 'app-footer'})
    footer.string = f'А1 АВТО · Снимок проверки {cycle_id} · Обновляется после завершения мониторинга.'
    page.main.append(footer)
    page.body.append(page.new_tag('script', src='static/monitoring.js', defer=''))
    return str(page), cycle_id


def export(base_url: str, output: Path, *, cycle_id: str | None = None,
           checks: list[str] | None = None) -> dict:
    # Local dashboard is the only request. No scan, mutation or remote workbook fetch.
    if urlsplit(base_url).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('Export requires the local monitoring app')
    if cycle_id and not re.fullmatch(r'[0-9a-f-]{36}', cycle_id):
        raise ValueError('Invalid cycle identifier')
    query = '?cycle_id=' + cycle_id if cycle_id else ''
    with urlopen(base_url.rstrip('/') + '/api/v1/dashboard/placement-identity' + query, timeout=60) as response:
        raw = response.read().decode('utf-8')
    html, actual_id = project_html(raw, checks=checks)
    if cycle_id and cycle_id != actual_id:
        raise ValueError('Rendered report belongs to another cycle')
    cycle_id = actual_id
    if output.exists():
        raise ValueError('Use a new output directory; existing snapshots are never overwritten')
    output.mkdir(parents=True)
    static = output / 'static'
    static.mkdir()
    for name in CSS:
        body = (ROOT / 'src/app/static' / name).read_text().replace("'/static/", "'./")
        (static / name).write_text(body)
    shutil.copytree(ROOT / 'src/app/static/fonts', static / 'fonts')
    js = (ROOT / 'src/app/static/monitoring.js').read_text()
    js = js.replace("document.getElementById('monitoring-evidence').open = true;",
                    "document.getElementById('business-review').scrollIntoView();")
    (static / 'monitoring.js').write_text(js)
    (static / 'shared.css').write_text(
        '.monitoring-workspace.shared-report .app-content{margin:0 auto!important;'
        'width:100%;max-width:1600px;padding:24px;box-sizing:border-box}'
        '.shared-report .workspace-navigation{display:flex;flex-wrap:wrap}'
        '.shared-report .workspace-bar{position:relative;top:auto}'
        '.monitoring-workspace .monitor-toolbar{position:static;top:auto;z-index:auto}'
        '.monitoring-workspace .vehicle-tile,.monitoring-workspace .brief-group,.monitoring-workspace .monitor-toolbar{scroll-margin-top:16px}'
        '.shared-history-entry a{display:inline-flex;align-items:center;min-height:44px;gap:8px;padding:10px 16px;border-radius:12px;background:#e8eefb;color:#284d8b;font-weight:650}'
        '@media(max-width:600px){.monitoring-workspace.shared-report .app-content{padding:12px}}'
    )
    (output / 'index.html').write_text(html, encoding='utf-8')
    (output / '.nojekyll').touch()
    manifest = {'schema_version': 1, 'scope': 'public-vehicle-dashboard', 'cycle_id': cycle_id,
                'scan_at_utc': scan_time(raw), 'incomplete_checks': checks or [],
                'published_at_utc': datetime.now(UTC).isoformat(), 'read_only': True,
                'contains_vehicle_details': True, 'live_updates': False,
                'files': {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(output.rglob('*')) if p.is_file()}}
    (output / 'publication.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18000')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--public-vehicle-details-approved', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.base_url, args.out.resolve()), indent=2))


if __name__ == '__main__':
    main()
