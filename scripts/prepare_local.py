"""Create a private local configuration once. Never overwrite an existing .env."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / '.env'
    if path.exists():
        print('LOCAL_CONFIG_EXISTS · текущая конфигурация сохранена')
        return
    password = secrets.token_hex(24)
    source = 'https://docs.google.com/spreadsheets/d/1afmXPsek-nu-1eH_n1PuYNIIIYcskJqwcSCIMHzJUNA/export?format=csv&gid=755848469'
    values = {
        'APP_ENV': 'stage', 'APP_TZ': 'Europe/Moscow', 'APP_BIND_PORT': '18000',
        'DB_BIND_PORT': '15433', 'NETWORK_PROFILE': 'local_vpn', 'AUTH_ENABLED': 'false',
        'DB_PASSWORD': password, 'DATABASE_DSN': f'postgresql+psycopg2://monitor:{password}@127.0.0.1:15433/a1_search_monitor',
        'SOURCE_IMPORT_SOURCE': 'sheet', 'SOURCE_GOOGLE_SHEET_EXPORT_URL': source,
        'SCHEDULER_ENABLED': 'false', 'DEALER_DISCOVERY_ENABLED': 'false',
        'SCAN_ENABLED_ENGINES': 'auto_ru,avito', 'SCAN_PAGES_LIMIT': '3',
        'SCAN_INTERVAL_MINUTES': '360', 'EVIDENCE_DIR': '/app/artifacts',
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
    print('LOCAL_CONFIG_CREATED · настройки созданы, пароль не выводится')


if __name__ == '__main__':
    main()
