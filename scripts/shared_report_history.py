"""Public historical snapshots and evidence-based activity, without marketplace requests."""
from __future__ import annotations

import hashlib
import json
import shutil
from uuid import UUID

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.service.monitoring_history import load_history, summarize_observations


def refresh_manifest(site, manifest):
    manifest['files'] = {p.relative_to(site).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(site.rglob('*')) if p.is_file() and p.name != 'publication.json'}
    (site / 'publication.json').write_text(json.dumps(manifest, indent=2) + '\n')


def prepare_history(root, site, manifest, base_url, *, export_fn, checks_fn):
    history = load_history(root / 'artifacts/evidence', until=manifest['scan_at_utc'])
    cache = root / 'artifacts/publication/history_cache_v2'
    cache.mkdir(parents=True, exist_ok=True)
    destination = site / 'history'
    destination.mkdir()
    available, unavailable = [], []
    current = manifest['cycle_id']
    for run in history['reports']:
        cid = run['cycle_id']
        target = destination / cid
        if cid == current:
            target.mkdir()
            shutil.copytree(site / 'static', target / 'static')
            shutil.copyfile(site / 'index.html', target / 'index.html')
        else:
            cached = cache / cid
            # Freeze historical render after initial export. Current-cycle updates
            # overwrite that snapshot, retaining its original observation dates.
            if not (cached / 'index.html').exists():
                try:
                    checks = checks_fn(root, cid) if (root / 'artifacts/evidence/cycles' / cid / 'report_bundle.ready.json').exists() else ['Архивная проверка: ограничения полноты сохранены в отчёте.']
                    export_fn(base_url, cached, cycle_id=cid, checks=checks)
                except (OSError, ValueError, RuntimeError):
                    if cached.exists():
                        shutil.rmtree(cached)
                    unavailable.append(cid)
                    continue
            shutil.copytree(cached, target)
        # Apply current shared layout fixes to archived snapshots without changing observations.
        if (site / 'static/shared.css').exists():
            shutil.copyfile(site / 'static/shared.css', target / 'static/shared.css')
        soup = BeautifulSoup((target / 'index.html').read_text(), 'html.parser')
        for a in soup.select('a[href]'):
            if a['href'].startswith('history.html'):
                a['href'] = '../../' + a['href']
        for note in soup.select('.monitor-note')[:1]:
            note.string = 'Архивная проверка. Дата наблюдений и ограничения указаны ниже; это не текущая ситуация.'
        link = soup.new_tag('a', href='../../index.html')
        link.string = 'Последний отчёт'
        if not soup.select_one('a[href="../../index.html"]'):
            soup.select_one('.workspace-navigation').append(link)
        (target / 'publication.json').unlink(missing_ok=True)
        if not soup.select_one('.archive-navigation'):
            nav = soup.new_tag('p', attrs={'class': 'archive-navigation shared-history-entry'})
            for href, title in [('../../index.html', 'Последний отчёт'), ('../../history.html', 'История проверок')]:
                a = soup.new_tag('a', href=href)
                a.string = title
                nav.append(a)
                nav.append(' · ')
            soup.select_one('main').insert(0, nav) if soup.select_one('main') else soup.insert(0, nav)
        (target / 'index.html').write_text(str(soup))
        run['href'] = f'history/{cid}/index.html'
        available.append(run)
        if cid == current:
            # Future runs will retain the most recently corrected version.
            cached = cache / cid
            if cached.exists():
                shutil.rmtree(cached)
            shutil.copytree(target, cached)
    accepted = {run['cycle_id'] for run in available}
    # Unrenderable legacy cycles must not silently contribute public activity days.
    observations = [event for row in history['rows'] for event in row['events'] if event['cycle_id'] in accepted]
    history.update(summarize_observations(observations))
    history['reports'] = available
    environment = Environment(loader=FileSystemLoader(root / 'src/app/templates'), autoescape=select_autoescape(['html']))
    page = environment.get_template('monitoring_history.html').render(
        history=history, latest_url='index.html', unavailable=unavailable)
    (site / 'history.html').write_text(page)
    (site / 'history-index.json').write_text(json.dumps(available, ensure_ascii=False, indent=2))
    manifest['history'] = {'reports': len(available), 'unavailable_reports': len(unavailable),
                           'activity_rows': len(history['rows']), 'day_basis': 'distinct_UTC_observation_dates'}
    refresh_manifest(site, manifest)


def preserve_archives(repo, site, manifest):
    """Retain publicly published dates even if the Mac later removes local evidence."""
    if not (site / 'history-index.json').exists():
        return
    old_index = repo / 'history-index.json'
    old = json.loads(old_index.read_text()) if old_index.exists() else []
    new = json.loads((site / 'history-index.json').read_text())
    known = {r['cycle_id'] for r in new}
    soup = BeautifulSoup((site / 'history.html').read_text(), 'html.parser')
    for row in old:
        cid = str(UUID(row['cycle_id']))
        if cid in known:
            continue
        source = repo / 'history' / cid
        if source.is_symlink() or not (source / 'index.html').is_file():
            raise ValueError('Published archive is unavailable')
        if any(p.is_symlink() for p in source.rglob('*')):
            raise ValueError('Symlink in published archive')
        shutil.copytree(source, site / 'history' / cid)
        if (site / 'static/shared.css').exists():
            shutil.copyfile(site / 'static/shared.css', site / 'history' / cid / 'static/shared.css')
        row['href'] = f'history/{cid}/index.html'
        new.append(row)
        li = soup.new_tag('li')
        a = soup.new_tag('a', href=row['href'])
        a.string = row['date'] + ' · сохранённый публичный архив'
        li.append(a)
        soup.select_one('#runs ul').append(li)
    new.sort(key=lambda r: (r['date'], r['cycle_id']), reverse=True)
    soup.select_one('#runs h2').string = f'Прошлые проверки · {len(new)}'
    (site / 'history.html').write_text(str(soup))
    (site / 'history-index.json').write_text(json.dumps(new, ensure_ascii=False, indent=2))
    manifest['history']['reports'] = len(new)
    refresh_manifest(site, manifest)
