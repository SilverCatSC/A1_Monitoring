"""Best-effort public snapshot update after a verified monitoring run.

Only the generated site is pushed to gh-pages. GitHub credentials remain in the
existing credential helper. This module never scans, changes VPN or sends chat.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID

from app.service.artifact_bundle import verify_bundle
from app.service.filters import CANONICAL_FILTERS, normalize_search_url
from app.service.search_followup import apply_search_followup
from scripts.export_shared_dashboard import export, scan_time
from scripts.local_api import env_values

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'SilverCatSC/A1_Monitoring'
REMOTE = f'https://github.com/{REPOSITORY}.git'
PUBLIC_URL = 'https://silvercatsc.github.io/A1_Monitoring/'
BRANCH = 'gh-pages'
CONFIG = Path('artifacts/publication/settings.json')


class PublicationError(RuntimeError):
    pass


def command(arguments: list[str], *, cwd: Path | None = None) -> str:
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GH_PROMPT_DISABLED': '1'}
    try:
        result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicationError('Publication command unavailable or timed out') from error
    if result.returncode:
        # Never echo credential-bearing helper output into reports or logs.
        raise PublicationError(f'{Path(arguments[0]).name} command failed; check GitHub sign-in and connection')
    return result.stdout.strip()


def publication_checks(root: Path, cycle_id: str) -> list[str]:
    base = root / 'artifacts/evidence/cycles' / cycle_id
    verify_bundle(base, 'report_bundle.ready.json', required=(
        'placement_reconciliation.json', 'analytical_summary.json', 'marketing_summary.txt'))
    report = json.loads((base / 'placement_reconciliation.json').read_text())
    if report.get('cycle_id') != cycle_id:
        raise ValueError('Report belongs to another cycle')
    apply_search_followup(report, base.parents[1])
    resolved = set()
    if report.get('search_followup'):
        payload = json.loads((base / 'search_followup/followup.json').read_text())
        resolved = {normalize_search_url(record['url']) for record in payload['records']}
    lines = []
    try:
        collection = json.loads((base / 'search_collection.json').read_text())
    except (OSError, ValueError):
        lines.append('Поисковые фильтры: полнота сохранённых данных не подтверждена.')
    else:
        if collection.get('cycle_id') != cycle_id or not collection.get('results'):
            raise ValueError('Search collection missing or belongs to another cycle')
        missing = [entry for entry in collection['results'] if entry['result'].get('complete') is not True
                   and normalize_search_url(entry['url']) not in resolved]
        lines.append(f"Поисковые фильтры завершены: {len(collection['results']) - len(missing)}"
                     f" из {len(collection['results'])}.")
        names = {(f.source.value, normalize_search_url(f.url)): f.name for f in CANONICAL_FILTERS}
        for entry in missing:
            label = names.get((entry['source'], normalize_search_url(entry['url'])), 'поисковый фильтр')
            platform = 'Auto.ru' if entry['source'] == 'auto_ru' else 'Avito'
            lines.append(f'Не завершено: {platform} — {label}. Позиции по этому фильтру не подтверждены.')
    for source, name in [('auto_ru', 'Auto.ru'), ('avito', 'Avito')]:
        if source not in report.get('catalogue_complete', {}):
            lines.append(f'{name}: площадка не проверялась в этом цикле.')
        elif not report.get('catalogue_enumeration_complete', {}).get(source):
            lines.append(f'{name}: каталог прочитан не полностью; отсутствие не доказывает неактивность.')
        elif not report['catalogue_complete'][source]:
            lines.append(f'{name}: каталог прочитан, но сопоставление карточек по unique_id не завершено.')
        if report.get('skipped_cards_by_source', {}).get(source) or source in report.get('blocked_sources', []):
            lines.append(f'{name}: есть непрочитанные карточки или блокировка доступа.')
    return lines


def protect_newer_snapshot(repo: Path, manifest: dict) -> None:
    previous_path = repo / 'publication.json'
    if not previous_path.exists():
        return
    previous = json.loads(previous_path.read_text())
    timestamp = previous.get('scan_at_utc') or scan_time((repo / 'index.html').read_text())
    old = datetime.fromisoformat(timestamp)
    new = datetime.fromisoformat(manifest['scan_at_utc'])
    if new < old or new == old and manifest['cycle_id'] != previous.get('cycle_id'):
        raise PublicationError('A newer scan is already public; refusing to replace it with an older report')


def push_snapshot(site: Path, manifest: dict, *, run_command=command) -> str:
    git = shutil.which('git') or '/usr/bin/git'
    gh = shutil.which('gh') or '/opt/homebrew/bin/gh'
    run_command([gh, 'auth', 'status', '--hostname', 'github.com'])
    with tempfile.TemporaryDirectory(prefix='a1-pages-update-') as temporary:
        repo = Path(temporary) / 'site'
        run_command([git, '-c', 'http.version=HTTP/1.1', 'clone', '--depth', '1', '--single-branch',
                     '--branch', BRANCH, REMOTE, str(repo)])
        protect_newer_snapshot(repo, manifest)
        from scripts.shared_report_history import preserve_archives
        preserve_archives(repo, site, manifest)
        for path in repo.iterdir():
            if path.name != '.git':
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        shutil.copytree(site, repo, dirs_exist_ok=True)
        run_command([git, 'add', '--all'], cwd=repo)
        run_command([git, '-c', 'user.name=A1 Monitoring', '-c', 'user.email=monitoring@users.noreply.github.com',
                     'commit', '-m', f'Update monitoring snapshot {manifest["cycle_id"]}'], cwd=repo)
        commit = run_command([git, 'rev-parse', 'HEAD'], cwd=repo)
        # Normal push preserves history and rejects concurrent/stale writes. No force push.
        run_command([git, '-c', 'http.version=HTTP/1.1', '-c', 'http.postBuffer=8388608',
                     'push', 'origin', f'HEAD:refs/heads/{BRANCH}'], cwd=repo)
        return commit


def write_status(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def confirm_deployment(manifest: dict, commit: str) -> bool:
    """Bounded anonymous readback; pending deployment is never called published."""
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urlopen(PUBLIC_URL + 'publication.json?deployment=' + commit, timeout=5) as response:
                remote = json.loads(response.read())
            if remote == manifest:
                with urlopen(PUBLIC_URL + '?deployment=' + commit, timeout=5) as response:
                    digest = hashlib.sha256(response.read()).hexdigest()
                if digest == manifest['files']['index.html']:
                    return True
        except (OSError, ValueError, URLError):
            pass
        time.sleep(3)
    return False


def publish_after_run(root: Path, cycle_id: str, run_directory: Path | None = None) -> dict:
    root = Path(root).resolve()
    identity = str(UUID(cycle_id))
    if identity != cycle_id:
        raise ValueError('Invalid cycle identifier')
    if run_directory is not None:
        run_directory = run_directory.resolve()
        if run_directory.parent != root / 'artifacts/host_runs' or not run_directory.is_dir():
            raise ValueError('Publication status must be within host_runs')
    config_path = root / CONFIG
    if not config_path.exists():
        return {'status': 'disabled', 'cycle_id': identity}
    status = {'status': 'failed', 'cycle_id': identity, 'public_url': PUBLIC_URL,
              'attempted_at_utc': datetime.now(UTC).isoformat()}
    try:
        config = json.loads(config_path.read_text())
        if config.get('enabled') is not True:
            return {'status': 'disabled', 'cycle_id': identity}
        if (config.get('scope') != 'public-vehicle-dashboard'
                or config.get('vehicle_details_approved') is not True or config.get('repository') != REPOSITORY):
            raise ValueError('Public vehicle details require the configured publication scope')
        with (config_path.parent / 'publish.lock').open('a') as lock:
            # Short nonblocking lock: do not hold up monitoring for another publisher.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checks = publication_checks(root, identity)
            port = int(env_values(root).get('APP_BIND_PORT', '18000'))
            with tempfile.TemporaryDirectory(prefix='snapshot-', dir=config_path.parent) as temporary:
                site = Path(temporary) / 'site'
                manifest = export(f'http://127.0.0.1:{port}', site, cycle_id=identity, checks=checks)
                from scripts.shared_report_history import prepare_history
                prepare_history(root, site, manifest, f'http://127.0.0.1:{port}',
                                export_fn=export, checks_fn=publication_checks)
                commit = push_snapshot(site, manifest)
            status.update(status='submitted', commit=commit, scan_at_utc=manifest['scan_at_utc'],
                          message='GitHub Pages is deploying; the public link updates after deployment.')
            if confirm_deployment(manifest, commit):
                status.update(status='published', message='Public report verified anonymously after deployment.')
    except (OSError, ValueError, RuntimeError) as error:
        status['message'] = str(error) if isinstance(error, (PublicationError, ValueError)) else 'Publication unavailable'
    write_status(config_path.parent / 'last_status.json', status)
    if run_directory:
        write_status(run_directory / 'publication_status.json', status)
    return status


def announce_publication(root: Path, cycle_id: str, run_directory: Path | None = None) -> dict:
    """Never change the scan/recovery outcome because a separate publication failed."""
    try:
        status = publish_after_run(root, cycle_id, run_directory)
    except Exception:  # best-effort integration; do not swallow scan failures
        status = {'status': 'failed', 'message': 'Publication unavailable; local report is unchanged'}
    if status['status'] in {'submitted', 'published'}:
        print(f'HOST_RUNNER_PUBLIC_REPORT_URL {PUBLIC_URL}', flush=True)
        print(f'HOST_RUNNER_PUBLIC_REPORT status={status["status"]} '
              f'deployment_pending={str(status["status"] == "submitted").lower()}', flush=True)
    elif status['status'] != 'disabled':
        print('HOST_RUNNER_PUBLIC_REPORT status=failed local_report_unchanged=true check_publication_status=true', flush=True)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--run-directory', type=Path)
    args = parser.parse_args()
    announce_publication(ROOT, args.cycle_id, args.run_directory)


if __name__ == '__main__':
    main()
