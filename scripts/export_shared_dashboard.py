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


def project_html(html: str) -> tuple[str, str]:
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
                           '<a href="#marketing-summary">Маркетингу</a></nav></header>'
                           '<p class="monitor-note">Сохранённый снимок последней опубликованной проверки. '
                           'Он не обновляется в реальном времени. Даты наблюдений и ограничения указаны ниже. '
                           'Непроверенное объявление не считается неактивным.</p>', 'html.parser')
    page.main.extend(list(header.contents))
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
        if href == '#monitoring-evidence':
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
    footer.string = f'А1 АВТО · Снимок проверки {cycle_id} · Публикуется вручную после мониторинга.'
    page.main.append(footer)
    page.body.append(page.new_tag('script', src='static/monitoring.js', defer=''))
    return str(page), cycle_id


def export(base_url: str, output: Path) -> dict:
    # Local dashboard is the only request. No scan, mutation or remote workbook fetch.
    if urlsplit(base_url).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('Export requires the local monitoring app')
    with urlopen(base_url.rstrip('/') + '/api/v1/dashboard/placement-identity', timeout=60) as response:
        raw = response.read().decode('utf-8')
    html, cycle_id = project_html(raw)
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
        '@media(max-width:600px){.monitoring-workspace.shared-report .app-content{padding:12px}}'
    )
    (output / 'index.html').write_text(html, encoding='utf-8')
    (output / '.nojekyll').touch()
    manifest = {'schema_version': 1, 'scope': 'public-vehicle-dashboard', 'cycle_id': cycle_id,
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
