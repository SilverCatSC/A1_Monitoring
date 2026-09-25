#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = PROJECT_ROOT / 'artifacts' / 'local_chrome_profile'
DEFAULT_EVIDENCE = PROJECT_ROOT / 'artifacts' / 'evidence'
DEFAULT_VPN_POLICY = PROJECT_ROOT / 'artifacts' / 'vpn_admission' / 'policy.json'
HOST_RUNNER_CONTEXT_ENV = 'A1_MONITORING_HOST_RUNNER_CONTEXT'
PACING_PROFILES = {
    'normal': {
        'SCAN_FILTER_PAUSE_MIN_SECONDS': '2',
        'SCAN_FILTER_PAUSE_MAX_SECONDS': '5',
        'SCAN_PAGE_PAUSE_MIN_SECONDS': '1',
        'SCAN_PAGE_PAUSE_MAX_SECONDS': '3',
        'AUTO_RU_PAGE_DELAY_SECONDS': '2.5',
        'AVITO_PAGE_DELAY_SECONDS': '2.5',
        'TARGET_CLOSED_RETRY_SECONDS': '3',
    },
    'cautious': {
        'SCAN_FILTER_PAUSE_MIN_SECONDS': '12',
        'SCAN_FILTER_PAUSE_MAX_SECONDS': '20',
        'SCAN_PAGE_PAUSE_MIN_SECONDS': '6',
        'SCAN_PAGE_PAUSE_MAX_SECONDS': '12',
        'AUTO_RU_PAGE_DELAY_SECONDS': '5',
        'AVITO_PAGE_DELAY_SECONDS': '4',
        'TARGET_CLOSED_RETRY_SECONDS': '5',
    },
}


def _env_file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _chrome_executable() -> str:
    candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ]
    if sys.platform == 'win32':
        program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        candidates = [
            str(Path(program_files) / 'Google/Chrome/Application/chrome.exe'),
            str(Path(program_files_x86) / 'Google/Chrome/Application/chrome.exe'),
            str(Path(local_app_data) / 'Google/Chrome/Application/chrome.exe') if local_app_data else '',
            str(Path(program_files) / 'Chromium/Application/chrome.exe'),
        ] + candidates
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    for name in ('google-chrome', 'google-chrome-stable', 'chrome', 'chromium', 'chromium-browser'):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError('Google Chrome or Chromium was not found')


def _cdp_ready(url: str) -> bool:
    try:
        with urlopen(f'{url.rstrip("/")}/json/version', timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def _ensure_cdp_page(cdp_url: str) -> bool:
    """Chrome can keep its debug endpoint alive after its final tab was closed."""
    try:
        with urlopen(f'{cdp_url.rstrip("/")}/json/list', timeout=2) as response:
            targets = json.load(response)
        if any(item.get('type') == 'page' for item in targets):
            return False
        request = Request(
            f'{cdp_url.rstrip("/")}/json/new?about%3Ablank', method='PUT'
        )
        with urlopen(request, timeout=2) as response:
            target = json.load(response)
        if target.get('type') != 'page':
            raise RuntimeError('Chrome did not create a controllable page')
        return True
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'Cannot prepare a Chrome tab at {cdp_url}: {exc}') from exc


def _ensure_local_chrome(cdp_url: str, profile: Path) -> bool:
    if _cdp_ready(cdp_url):
        _ensure_cdp_page(cdp_url)
        return False
    parsed = urlparse(cdp_url)
    if parsed.hostname not in {'127.0.0.1', 'localhost'} or not parsed.port:
        raise RuntimeError('Local Chrome endpoint must use 127.0.0.1 or localhost with a port')

    profile.mkdir(parents=True, exist_ok=True)
    command = [
        _chrome_executable(),
        f'--remote-debugging-port={parsed.port}',
        '--remote-debugging-address=127.0.0.1',
        f'--user-data-dir={profile}',
        '--no-first-run',
        '--no-default-browser-check',
        'about:blank',
    ]
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        if _cdp_ready(cdp_url):
            _ensure_cdp_page(cdp_url)
            return True
        time.sleep(1)
    raise RuntimeError('Local Chrome started but its control endpoint did not become ready')


def _configure_runtime(args: argparse.Namespace, env_values: dict[str, str]) -> str:
    cdp_url = f'http://127.0.0.1:{args.cdp_port}'
    evidence_dir = Path(args.evidence_dir).expanduser().resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    os.environ['NETWORK_PROFILE'] = 'local_browser'
    os.environ['BROWSER_CDP_URL'] = cdp_url
    os.environ['PLAYWRIGHT_HEADLESS'] = 'false'
    os.environ['SCAN_ENABLED_ENGINES'] = args.engines
    # Every full host cycle must synchronize exact-ID links before search.
    # Probe mode never writes a cycle or registry link.
    os.environ['PLACEMENT_RECONCILIATION_ENABLED'] = 'false' if args.probe_url else 'true'
    os.environ['SCAN_PAGES_LIMIT'] = str(args.pages)
    os.environ['EVIDENCE_DIR'] = str(evidence_dir)
    # The host runner owns this file outside the writable evidence directory.
    # A web-container API cannot opt into host-browser admission by itself.
    os.environ['VPN_OPERATIONAL_POLICY_PATH'] = str(DEFAULT_VPN_POLICY)
    for key, value in PACING_PROFILES[args.pace].items():
        os.environ[key] = value

    if not args.probe_url:
        password = env_values.get('DB_PASSWORD')
        if not password:
            raise RuntimeError('DB_PASSWORD is missing in .env')
        port = env_values.get('DB_BIND_PORT', '5433')
        os.environ['DATABASE_DSN'] = (
            'postgresql+psycopg2://monitor:'
            f'{quote_plus(password)}@127.0.0.1:{port}/a1_search_monitor'
        )
    return cdp_url


def _require_vpn_admission() -> None:
    """Fail before Chrome or a DB context unless owner policy and VPSUS are ready."""
    from app.service.vpn_admission import require_operational_vpn_admission

    require_operational_vpn_admission(DEFAULT_VPN_POLICY)
    print('VPN_OPERATIONAL_ADMISSION_OK vpn=connected routes=declared egress=unverified')


def _prepare_vpn_admission_directory() -> None:
    from app.service.vpn_admission import prepare_attestation_directory

    prepare_attestation_directory(DEFAULT_VPN_POLICY.parent)


def _require_interactive_host_runner_context() -> None:
    """Reject direct host scans that bypass an accepted interactive runner."""
    if sys.platform == 'win32':
        raise RuntimeError(
            'Windows full monitoring is not accepted; use the MacBook production host'
        )
    if sys.platform != 'darwin':
        return
    if os.environ.get(HOST_RUNNER_CONTEXT_ENV) != '1':
        raise RuntimeError('full monitoring requires run_monitoring_host_macos.sh')
    _require_verified_host_lock_context()
    os.environ['LOCAL_BROWSER_HOST_ADMISSION'] = 'true'


def _require_verified_host_lock_context() -> None:
    """Bind the host-child process to the runner's inherited kernel lock.

    The marker blocks accidental direct entry points; the inherited descriptor
    additionally proves that this child owns the same lock as the Mac runner.
    It is not a hostile-same-user security boundary, but it prevents a normal
    shell invocation from silently becoming a second full browser cycle.
    """
    from app.service.host_runner_context import (
        HostRunnerContextError,
        require_verified_macos_host_runner_context,
    )

    try:
        require_verified_macos_host_runner_context()
    except HostRunnerContextError as exc:
        raise RuntimeError('full monitoring requires the verified MacBook host lock') from exc


async def _probe(url: str, pages: int) -> dict:
    host = (urlparse(url).hostname or '').lower()
    if host == 'avito.ru' or host.endswith('.avito.ru'):
        from app.scraper.avito import AvitoAdapter

        adapter = AvitoAdapter()
    elif host == 'auto.ru' or host.endswith('.auto.ru'):
        from app.scraper.auto_ru import AutoRuAdapter

        adapter = AutoRuAdapter()
    else:
        raise RuntimeError('Probe URL must belong to avito.ru or auto.ru')

    result = await adapter.scan_filter(url, max_pages=pages)
    payload = asdict(result)
    payload['scanned_at'] = result.scanned_at.isoformat()
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Run A1 monitoring through visible Chrome with ephemeral private contexts.'
    )
    parser.add_argument('--engines', default='auto_ru,avito')
    parser.add_argument('--pages', type=int, default=3, choices=range(1, 11), metavar='1..10')
    parser.add_argument('--probe-url', help='Check one search URL without writing observations to the DB')
    parser.add_argument(
        '--placement-identity', action='store_true',
        help='Compatibility flag; exact-ID link reconciliation now runs before every full cycle',
    )
    parser.add_argument(
        '--retry-cycle',
        help='Start one explicit retry for a completed partial/failed cycle; never combine with --watch.',
    )
    parser.add_argument('--cdp-port', type=int, default=19222)
    parser.add_argument('--browser-profile', default=str(DEFAULT_PROFILE))
    parser.add_argument('--evidence-dir', default=str(DEFAULT_EVIDENCE))
    parser.add_argument(
        '--pace',
        choices=tuple(PACING_PROFILES),
        default='cautious',
        help='Request pacing profile; cautious is slower and is the local default',
    )
    parser.add_argument('--full-json', action='store_true', help='Print complete hit payloads')
    parser.add_argument(
        '--watch',
        action='store_true',
        help='Keep running on this computer and repeat the full scan on schedule',
    )
    parser.add_argument('--interval-minutes', type=int, default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    # Windows/MSI is deliberately source-only fallback material after the
    # MacBook primary-host decision. Reject even the read-only probe path here
    # because this raw Python entry point could otherwise bypass its PowerShell
    # wrappers and make live marketplace traffic from an unaccepted host.
    if sys.platform == 'win32':
        raise RuntimeError('Windows monitoring is not accepted; use the MacBook production host')
    if args.retry_cycle and args.probe_url:
        raise RuntimeError('retry-cycle cannot be combined with probe-url')
    if args.retry_cycle and args.watch:
        raise RuntimeError('retry-cycle cannot be combined with watch')
    if args.placement_identity and args.probe_url:
        raise RuntimeError('placement-identity requires a full controlled cycle')
    if args.watch:
        raise RuntimeError(
            'recurring monitoring is not accepted; use a reviewed MacBook LaunchAgent after M7 acceptance'
        )
    if not args.probe_url:
        _require_interactive_host_runner_context()
    env_values = _env_file_values(PROJECT_ROOT / '.env')
    cdp_url = _configure_runtime(args, env_values)
    if args.probe_url:
        started = _ensure_local_chrome(cdp_url, Path(args.browser_profile).expanduser().resolve())
        print('LOCAL_CHROME_STARTED' if started else 'LOCAL_CHROME_REUSED')
        print(f'LOCAL_SCAN_PACE {args.pace}')
        payload = asyncio.run(_probe(args.probe_url, args.pages))
        if args.full_json:
            printable = payload
        else:
            printable = {
                'complete': payload['complete'],
                'error': payload['error'],
                'pages_scanned': payload['page_count'],
                'hits': len(payload['hits']),
                'diagnostics': payload['diagnostics'],
                'sample': [
                    {
                        'title': hit['title'],
                        'page': hit['page_number'],
                        'position': hit['position'],
                        'price': hit['price'],
                        'url': hit['url'].split('?', 1)[0],
                    }
                    for hit in payload['hits'][:10]
                ],
            }
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        if payload['complete']:
            print(f'LOCAL_PROBE_OK pages={payload["page_count"]} hits={len(payload["hits"])}')
            return 0
        print(f'LOCAL_PROBE_BLOCKED error={payload["error"]}')
        return 2

    _prepare_vpn_admission_directory()
    from app.db import get_db_context
    from app.service.cycle import MonitoringCycleService
    from app.service.scan_progress import ScanProgressTracker

    interval_minutes = args.interval_minutes or int(env_values.get('SCAN_INTERVAL_MINUTES', '360'))
    if interval_minutes < 1:
        raise RuntimeError('interval-minutes must be positive')

    exit_code = 0
    progress = ScanProgressTracker(str(Path(args.evidence_dir).expanduser().resolve()))
    def start_browser():
        started = _ensure_local_chrome(cdp_url, Path(args.browser_profile).expanduser().resolve())
        print('LOCAL_CHROME_STARTED' if started else 'LOCAL_CHROME_REUSED')
        print(f'LOCAL_SCAN_PACE {args.pace}')
    while True:
        # Recheck immediately before the DB-writing cycle. Probe mode remains
        # deliberately outside this full-cycle admission boundary.
        _require_vpn_admission()
        with get_db_context() as db:
            try:
                service = MonitoringCycleService(
                    db, progress_callback=progress, before_browser=start_browser
                )
                result = (
                    service.retry(args.retry_cycle)
                    if args.retry_cycle
                    else service.run()
                )
            except Exception as exc:
                print(f'LOCAL_CYCLE_FAILED {type(exc).__name__}: {exc}', file=sys.stderr)
                return 1
        summary = result['scan']
        completion = result['completion']
        cycle = result['cycle']
        print(f'LOCAL_CYCLE_ID {cycle["id"]}')
        print('LOCAL_SELLER_PREFLIGHT ' + json.dumps(result['seller_preflight'], ensure_ascii=False))
        print('LOCAL_DIRECT_CARDS ' + json.dumps(result['direct_cards'], ensure_ascii=False))
        if 'placement_reconciliation' in result:
            print('LOCAL_PLACEMENT_RECONCILIATION '
                  + json.dumps(result['placement_reconciliation'], ensure_ascii=False))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print('LOCAL_CYCLE_COMPLETION ' + json.dumps(completion, ensure_ascii=False))
        if completion['status'] != 'completed':
            print(
                'LOCAL_SCAN_PARTIAL '
                f'technical_errors={completion["technical_errors"]} '
                f'links_need_review={completion["links_need_review"]} '
                f'direct_cards_incomplete={completion["direct_cards_incomplete"]}'
            )
            exit_code = 2
        else:
            print(
                'LOCAL_SCAN_OK '
                f'filters={summary["filters_scanned"]} found={summary["found"]} '
                f'missed={summary["missed_confirmed"] + summary["missed_uncertain"]}'
            )
            exit_code = 0
        if not args.watch:
            return exit_code
        next_run = datetime.now().astimezone() + timedelta(minutes=interval_minutes)
        print(f'LOCAL_MONITOR_WAIT next_run={next_run.isoformat(timespec="minutes")}')
        time.sleep(interval_minutes * 60)


if __name__ == '__main__':
    def _sigterm_as_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_as_interrupt)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('LOCAL_SCAN_INTERRUPTED', file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        print(f'LOCAL_SCAN_FAILED {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
