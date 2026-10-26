"""Offline acceptance of saved observations. Never fetches sources or updates a cycle."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.models import EngineType
from app.scraper.base import canonical_listing_key
from app.service.automatic_content import automatic_content_review
from app.service.catalogue_visibility import add_catalogue_visibility
from app.service.evidence import EvidenceAccessError, read_direct_card_evidence
from app.service.marketing_content import add_marketing_comparison
from app.service.placement_feed_snapshot import PlacementFeedSnapshot
from app.service.publication_alerts import publication_alerts


def check_saved_report(report_path, evidence_dir, *, snapshot_path=None, search_path=None):
    """Replay local data only; keep integrity faults separate from business findings."""
    paths = [Path(report_path)]
    if snapshot_path:
        paths.append(Path(snapshot_path))
    if search_path:
        paths.append(Path(search_path))
    raw = {str(path): path.read_bytes() for path in paths}
    report = json.loads(raw[str(paths[0])])
    if (not isinstance(report, dict) or report.get('schema_version') != 1
            or not isinstance(report.get('cycle_id'), str)
            or not isinstance(report.get('observed_cards'), list)):
        raise ValueError('invalid saved report schema')
    cards = report['observed_cards']
    problems = []
    counts = Counter()
    seen = {}
    for index, saved in enumerate(cards):
        def problem(code, *, saved=saved, index=index):
            problems.append({'card_index': index, 'source': saved.get('source'),
                             'url': saved.get('url'), 'code': code})
        source = saved.get('source')
        if source not in {'auto_ru', 'avito'}:
            problem('invalid_source')
            continue
        counts[source + ':' + str(saved.get('state'))] += 1
        key = canonical_listing_key(EngineType(source), saved.get('url'))
        if not key:
            problem('invalid_advert_url')
            continue
        signature = (saved.get('state'), (saved.get('card') or {}).get('placement_id'))
        if key in seen and seen[key] != signature:
            problem('conflicting_duplicate_observations')
        seen[key] = signature
        if saved.get('state') not in {'active', 'removed'}:
            continue
        try:
            read_direct_card_evidence(saved, str(evidence_dir))
        except EvidenceAccessError:
            problem('card_evidence_unverified')
        card = saved.get('card') or {}
        if saved.get('state') == 'active':
            description = card.get('description') or ''
            capture = card.get('description_capture') or {}
            if (not description or capture.get('software_truncated') is not False
                    or capture.get('sha256') != hashlib.sha256(description.encode()).hexdigest()):
                problem('description_incomplete_or_unverified')
    working = copy.deepcopy(report)
    search_coverage = []
    if search_path:
        collection = json.loads(raw[str(Path(search_path))])
        if collection.get('cycle_id') != report['cycle_id']:
            raise ValueError('search collection belongs to another cycle')
        for entry in collection.get('results', []):
            result = entry['result']
            search_coverage.append({
                'source': entry['source'], 'filter_id': entry['filter_id'],
                'url': entry['url'], 'pages_limit': entry['pages_limit'],
                'pages_read': result['page_count'], 'complete': result['complete'],
                'exhausted': result.get('exhausted'), 'error': result.get('error'),
            })
            if (result['complete'] and (result.get('error')
                    or (result['page_count'] < entry['pages_limit'] and not result.get('exhausted')))):
                problems.append({'code': 'search_completion_unproven', 'source': entry['source'],
                                 'url': entry['url'], 'filter_id': entry['filter_id']})
        add_catalogue_visibility(working, evidence_dir, collection=collection)
    content = automatic_content_review(working, evidence_dir)
    if snapshot_path:
        snapshot = PlacementFeedSnapshot(**json.loads(raw[str(Path(snapshot_path))]))
        add_marketing_comparison(content, working['observed_cards'], snapshot, evidence_dir=evidence_dir)
    for path in paths:
        if path.read_bytes() != raw[str(path)]:
            raise ValueError('input changed during offline check')
    return {
        'schema_version': 1, 'cycle_id': report['cycle_id'],
        'checked_at_utc': datetime.now(UTC).isoformat(),
        'mode': 'offline_saved_data', 'new_live_scan': False, 'source_writes': False,
        'live_acceptance': 'not_run',
        'historical_report_created_at_utc': report.get('created_at_utc'),
        'input_sha256': {path: hashlib.sha256(data).hexdigest() for path, data in raw.items()},
        'source_inputs_unchanged': True,
        'integrity_status': 'needs_attention' if problems else 'passed',
        'integrity_problems': problems, 'observed_card_counts': dict(counts),
        'catalogue_enumeration_complete': report.get('catalogue_enumeration_complete', {}),
        'catalogue_direct_checks_complete': report.get('catalogue_complete', {}),
        'search_coverage': search_coverage,
        'saved_search_positions': [
            {'source': c['source'], 'url': c['url'], 'positions': c.get('search_positions', [])}
            for c in working['observed_cards']
        ],
        'content_review': content,
        'within_snapshot_publication_alerts': publication_alerts(working),
        'limitations': [
            'Проверены сохранённые наблюдения, а не текущее состояние площадок.',
            'Таблица и фиды используются только из указанного локального снимка на его дату.',
            'Без предыдущего снимка исчезновения и перевыкладки не переоцениваются.',
            'Полнота каталога и чтение карточек показаны отдельно; это не разрешение на сдачу.',
        ],
    }


def render_saved_check(result):
    def escape(value):
        return html.escape(str(value))
    content = result['content_review']
    parts = [
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Локальная проверка мониторинга</title>',
        '<style>body{font:16px/1.6 system-ui;max-width:1100px;margin:35px auto;padding:0 24px}'
        'td,th{padding:8px;border:1px solid #ccc;text-align:left}table{border-collapse:collapse}'
        '.note{background:#fff2cf;padding:16px}a{overflow-wrap:anywhere}</style>',
        '<h1>Локальная проверка сохранённых данных</h1>',
        '<p class="note">Живой прогон не выполнялся. Источники и исходный отчёт не изменены. '
        'Результат не подтверждает текущую доступность объявлений.</p>',
        '<p>Цикл: ' + escape(result['cycle_id']) + '<br>Снимок отчёта: '
        + escape(result['historical_report_created_at_utc']) + '<br>Целостность: '
        + escape(result['integrity_status']) + '</p>',
        '<h2>Покрытие сохранённого цикла</h2><table><tr><th>Площадка</th>'
        '<th>Каталог перечислен полностью</th><th>Карточки дочитаны</th></tr>',
    ]
    for source in ('auto_ru', 'avito'):
        def flag(mapping, *, source=source):
            value = result[mapping].get(source)
            return 'Да' if value is True else 'Нет' if value is False else 'Не подтверждено'
        parts.append('<tr><td>' + source + '</td><td>' + flag('catalogue_enumeration_complete')
                     + '</td><td>' + flag('catalogue_direct_checks_complete') + '</td></tr>')
    parts += ['</table><h2>Сохранённая выдача по фильтрам</h2>',
              '<table><tr><th>Фильтр</th><th>Страниц прочитано / предел</th>'
              '<th>Поиск завершён</th><th>Выдача закончилась раньше</th></tr>']
    for search in result['search_coverage']:
        parts.append('<tr><td>' + escape(search['source']) + ' · <a href="'
                     + escape(search['url']) + '">' + escape(search['filter_id'])
                     + '</a></td><td>' + escape(search['pages_read']) + ' / '
                     + escape(search['pages_limit']) + '</td><td>'
                     + ('Да' if search['complete'] else 'Нет') + '</td><td>'
                     + ('Да' if search['exhausted'] else 'Нет') + '</td></tr>')
    parts += ['</table><h2>Проверка содержания</h2><p>Карточек: '
              + escape(content['summary']['cards_checked']) + '; с предупреждениями: '
              + escape(content['summary']['cards_with_warnings']) + '.</p>',
              '<h2>Технические проблемы сохранённых данных</h2><ul>']
    for problem in result['integrity_problems']:
        parts.append('<li>' + escape(problem['code']) + ': ' + escape(problem['url']) + '</li>')
    if not result['integrity_problems']:
        parts.append('<li>Не обнаружены в проверенных снимках.</li>')
    parts += ['</ul><h2>Замечания по историческим карточкам</h2>',
              '<p>Это результаты на дату снимка; часть замечаний могла быть исправлена позднее.</p>']
    for card in content['cards']:
        findings = [f for f in card['findings'] if f['severity'] == 'warning']
        if not findings:
            continue
        parts.append('<details><summary>' + escape(card['source']) + ' · '
                     + escape(card.get('unique_id') or 'unique_id не подтверждён') + ' · '
                     + escape(card['title']) + '</summary><p><a href="'
                     + escape(card['url']) + '">Объявление</a> · Снимок: '
                     + escape(card['captured_at_utc']) + '</p><ul>')
        for finding in findings:
            parts.append('<li><b>' + escape(finding['title']) + '</b>: '
                         + escape(finding['detail']) + ' ' + escape(finding['action']) + '</li>')
        parts.append('</ul></details>')
    parts += ['<h2>Ограничения</h2><ul>']
    parts += ['<li>' + escape(line) + '</li>' for line in result['limitations']]
    parts += ['</ul><p>Подробные поля, позиции, unique_id и расхождения — в соседнем JSON-файле.</p></html>']
    return ''.join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--search', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    # Output may never replace the source report, snapshot, or search collection.
    output = args.output.resolve()
    destinations = {output, output.with_suffix('.html')}
    inputs = {path.resolve() for path in (args.report, args.snapshot, args.search) if path}
    if destinations & inputs or output.suffix != '.json':
        parser.error('output must be a new .json destination separate from input files')
    try:
        result = check_saved_report(args.report, args.evidence_dir,
                                    snapshot_path=args.snapshot, search_path=args.search)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, 'Saved-data check failed: ' + str(exc) + '\n')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    output.with_suffix('.html').write_text(render_saved_check(result), encoding='utf-8')
    print('OFFLINE_CHECK ' + result['integrity_status'] + ' ' + str(output))
    return 0 if result['integrity_status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
