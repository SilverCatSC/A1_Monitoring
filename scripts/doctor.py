"""Non-mutating local readiness and UI checks; never contacts marketplaces."""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

if __package__:
    from .local_scan import _chrome_executable
else:
    from local_scan import _chrome_executable

ROOT = Path(__file__).resolve().parents[1]


def compose_available():
    if shutil.which('docker-compose') or shutil.which('docker'):
        return True
    if sys.platform == 'win32':
        local_app_data = Path.home() / 'AppData' / 'Local'
        docker = local_app_data / 'Programs' / 'DockerDesktop' / 'resources' / 'bin' / 'docker.exe'
        return docker.is_file()
    return False


def env_values():
    if not (ROOT / '.env').exists():
        return {}
    return {line.split('=', 1)[0]: line.split('=', 1)[1].strip().strip('\"\'')
            for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines()
            if '=' in line and not line.strip().startswith('#')}


def chrome_available():
    try:
        _chrome_executable()
        return True
    except RuntimeError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--http', action='store_true')
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    values = env_values()
    checks = {
        'Python 3.12': sys.version_info[:2] == (3, 12),
        'Настройки .env': bool(values.get('DB_PASSWORD') and values.get('SOURCE_GOOGLE_SHEET_EXPORT_URL')),
        'Google Chrome': chrome_available(),
        'Compose': compose_available(),
        'Автоскан в контейнере отключён': values.get('SCHEDULER_ENABLED', 'false').lower() == 'false',
    }
    deps = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True)
    checks['Совместимость зависимостей'] = deps.returncode == 0
    base = f'http://127.0.0.1:{values.get("APP_BIND_PORT", "18000")}'
    if args.http:
        for _attempt in range(30 if args.wait else 1):
            try:
                with urlopen(base + '/api/v1/ready', timeout=2) as response:
                    ready = json.load(response)
                checks['Приложение и база данных'] = ready.get('database') == 'ok'
                break
            except (OSError, ValueError):
                checks['Приложение и база данных'] = False
                if args.wait:
                    time.sleep(1)
        for path in ['/api/v1/dashboard', '/api/v1/dashboard/placements', '/api/v1/dashboard/analytics',
                     '/api/v1/dashboard/activity', '/api/v1/dashboard/listings', '/api/v1/dashboard/history',
                     '/api/v1/dashboard/feedback', '/api/v1/dashboard/settings', '/api/v1/dashboard/reconciliation', '/static/app.css', '/static/progress.js', '/static/reconciliation.js']:
            try:
                with urlopen(base + path, timeout=5) as response:
                    checks[path] = response.status == 200
            except OSError:
                checks[path] = False
    for name, ok in checks.items():
        print(f'{"OK" if ok else "FAIL"} · {name}')
    print('LOCAL_READY' if all(checks.values()) else 'LOCAL_NEEDS_ATTENTION')
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
