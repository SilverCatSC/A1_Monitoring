"""Send a public report link to a Bitrix24 incoming webhook.

Dry-run is the default. The webhook is supplied by an argument or environment
variable and is never printed. This script does not create or alter Bitrix
configuration; an administrator creates the incoming webhook once.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _valid_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError(f'{label} должен быть HTTPS URL')
    return value


def _message(report_url: str, custom: str) -> str:
    if custom.strip():
        return custom.strip() + '\n' + report_url
    stamp = datetime.now(timezone.utc).astimezone().strftime('%d.%m.%Y %H:%M')
    return f'A1 Search Monitor · отчёт {stamp}\n{report_url}'


def main() -> int:
    parser = argparse.ArgumentParser(description='Publish an A1 report link to Bitrix24.')
    parser.add_argument('--report-url', required=True)
    parser.add_argument('--message', default='')
    parser.add_argument('--webhook-url', default='')
    parser.add_argument('--dialog-id', default='')
    parser.add_argument('--send', action='store_true', help='send the request; otherwise print a safe dry-run')
    parser.add_argument('--output', default='artifacts/bitrix_publish_last.json')
    args = parser.parse_args()

    values = _env_values(Path('.env'))
    report_url = _valid_url(args.report_url, 'report-url')
    webhook = args.webhook_url or os.environ.get('BITRIX_WEBHOOK_URL') or values.get('BITRIX_WEBHOOK_URL', '')
    dialog = args.dialog_id or os.environ.get('BITRIX_DIALOG_ID') or values.get('BITRIX_DIALOG_ID', '')
    if args.send:
        if not webhook:
            raise ValueError('Для --send нужен BITRIX_WEBHOOK_URL или --webhook-url')
        if not dialog:
            raise ValueError('Для --send нужен BITRIX_DIALOG_ID или --dialog-id')
        webhook = _valid_url(webhook, 'webhook-url')

    payload = {'DIALOG_ID': dialog, 'MESSAGE': _message(report_url, args.message)}
    record = {'mode': 'send' if args.send else 'dry_run', 'report_url': report_url,
              'dialog_configured': bool(dialog), 'created_at': datetime.now(timezone.utc).isoformat()}
    if args.send:
        request = Request(webhook, data=json.dumps(payload).encode('utf-8'),
                           headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=20) as response:
            response_payload = json.loads(response.read().decode('utf-8'))
        if response_payload.get('error'):
            raise RuntimeError(f"Bitrix error: {response_payload.get('error_description', response_payload['error'])}")
        record['bitrix'] = {'result': response_payload.get('result'), 'time': response_payload.get('time')}
        print('BITRIX_PUBLISH_OK')
    else:
        print(json.dumps({'mode': 'dry_run', 'payload': payload, 'dialog_configured': bool(dialog)},
                         ensure_ascii=False, indent=2))
        print('BITRIX_DRY_RUN_OK')

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f'BITRIX_PUBLISH_FAILED {exc}', file=sys.stderr)
        raise SystemExit(1) from None
