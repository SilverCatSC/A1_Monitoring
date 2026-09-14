"""Non-mutating local readiness and UI checks; never contacts marketplaces."""
import argparse
import json
import shutil
import subprocess
import sys
import time

if __package__:
    from .local_api import (
        LocalApiAuthenticationError,
        env_values,
        local_api_auth_headers,
        local_api_request,
        open_local_api,
    )
    from .local_scan import _chrome_executable
else:
    from local_api import (
        LocalApiAuthenticationError,
        env_values,
        local_api_auth_headers,
        local_api_request,
        open_local_api,
    )
    from local_scan import _chrome_executable


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
        'Compose': bool(shutil.which('docker-compose') or shutil.which('docker')),
        'Автоскан в контейнере отключён': values.get('SCHEDULER_ENABLED', 'false').lower() == 'false',
    }
    deps = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True)
    checks['Совместимость зависимостей'] = deps.returncode == 0
    base = f'http://127.0.0.1:{values.get("APP_BIND_PORT", "18000")}'
    if args.http:
        try:
            protected_headers = local_api_auth_headers(values)
        except LocalApiAuthenticationError:
            # Do not print the username, password, or Basic token.  A protected
            # dashboard without a local admin credential is not ready for a
            # host-runner or authenticated UI check.
            checks['Учётные данные local API'] = False
            protected_headers = None
        for _attempt in range(30 if args.wait else 1):
            try:
                with open_local_api(
                    local_api_request(base + '/api/v1/ready'), timeout=2
                ) as response:
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
            if protected_headers is None:
                checks[path] = False
                continue
            try:
                with open_local_api(
                    local_api_request(base + path, headers=protected_headers), timeout=5
                ) as response:
                    checks[path] = response.status == 200
            except (OSError, ValueError):
                checks[path] = False
    for name, ok in checks.items():
        print(f'{"OK" if ok else "FAIL"} · {name}')
    print('LOCAL_READY' if all(checks.values()) else 'LOCAL_NEEDS_ATTENTION')
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
