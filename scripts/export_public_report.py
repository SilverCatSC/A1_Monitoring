"""Export only an owner-approved, aggregate-only public A1 report."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPORT_PATH = '/api/v1/public/report'
ALLOWLIST_SCOPE = 'a1-aggregate-public-report-v1'
MAX_ALLOWLIST_AGE = timedelta(days=31)


class PublicationRejected(RuntimeError):
    pass


def load_allowlist(path: Path, *, now: datetime | None = None) -> dict:
    """Validate the small, local owner approval required for every export."""
    current = now or datetime.now(UTC)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationRejected('publication allowlist must be readable valid JSON') from exc
    if not isinstance(payload, dict) or payload.get('scope') != ALLOWLIST_SCOPE:
        raise PublicationRejected('allowlist scope must approve only the aggregate public report')
    approved_by = str(payload.get('approved_by') or '').strip()
    if not approved_by:
        raise PublicationRejected('allowlist must name its approver')
    try:
        approved_at = datetime.fromisoformat(str(payload['approved_at']).replace('Z', '+00:00'))
        expires_at = datetime.fromisoformat(str(payload['expires_at']).replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicationRejected('allowlist needs ISO approved_at and expires_at') from exc
    if approved_at.tzinfo is None or expires_at.tzinfo is None:
        raise PublicationRejected('allowlist timestamps must include a timezone')
    approved_at = approved_at.astimezone(UTC)
    expires_at = expires_at.astimezone(UTC)
    if approved_at > current or expires_at <= current or expires_at - approved_at > MAX_ALLOWLIST_AGE:
        raise PublicationRejected('allowlist is expired, future-dated, or older than 31 days')
    return {
        'scope': ALLOWLIST_SCOPE,
        'approved_by': approved_by,
        'approved_at': approved_at.isoformat(),
        'expires_at': expires_at.isoformat(),
    }


def _authorization_from_environment(name: str | None) -> str | None:
    if not name:
        return None
    raw = os.environ.get(name)
    if not raw:
        raise PublicationRejected(f'authentication environment variable {name} is not set')
    encoded = base64.b64encode(raw.encode('utf-8')).decode('ascii')
    return f'Basic {encoded}'


def _fetch(base_url: str, path: str, *, authorization: str | None) -> bytes:
    headers = {'User-Agent': 'A1-public-report-export/2'}
    if authorization:
        headers['Authorization'] = authorization
    request = Request(urljoin(base_url.rstrip('/') + '/', path.lstrip('/')), headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise PublicationRejected(f'HTTP {response.status} for approved public report')
            return response.read()
    except HTTPError as exc:
        raise PublicationRejected(f'HTTP {exc.code} for approved public report') from exc
    except URLError as exc:
        raise PublicationRejected('approved public report is not reachable') from exc


def _validate_html(body: str) -> None:
    lowered = body.casefold()
    forbidden = ('<script', '/api/v1/', '/evidence', 'manager_feedback', 'source_auto_ru', 'source_avito')
    if any(token in lowered for token in forbidden):
        raise PublicationRejected('public report contains a forbidden internal reference')


def _clean_previous_export(output: Path) -> None:
    if output == PROJECT_ROOT:
        raise PublicationRejected('output directory must not be the project root')
    output.mkdir(parents=True, exist_ok=True)
    allowed = {'index.html', 'PUBLICATION_MANIFEST.json', 'static'}
    unexpected = sorted(item.name for item in output.iterdir() if item.name not in allowed)
    if unexpected:
        raise PublicationRejected(
            'output directory has unexpected files; move them before public export: '
            + ', '.join(unexpected)
        )
    for target in (output / 'index.html', output / 'PUBLICATION_MANIFEST.json'):
        if target.is_file():
            target.unlink()
    static = output / 'static'
    if static.is_dir():
        shutil.rmtree(static)


def main() -> int:
    parser = argparse.ArgumentParser(description='Export one explicitly approved aggregate public report.')
    parser.add_argument('--base-url', default='http://127.0.0.1:18000')
    parser.add_argument('--out', default='public')
    parser.add_argument('--allowlist', required=True, help='Local approval JSON; never commit it')
    parser.add_argument(
        '--auth-env',
        default=None,
        help='Optional environment variable holding username:password for local Basic-auth fetch',
    )
    args = parser.parse_args()

    approval = load_allowlist(Path(args.allowlist).expanduser().resolve())
    authorization = _authorization_from_environment(args.auth_env)
    output = Path(args.out).expanduser().resolve()
    _clean_previous_export(output)

    body = _fetch(args.base_url, PUBLIC_REPORT_PATH, authorization=authorization).decode('utf-8')
    _validate_html(body)
    static_source = PROJECT_ROOT / 'src' / 'app' / 'static'
    shutil.copytree(static_source, output / 'static')
    body = body.replace('href="/static/', 'href="static/')
    (output / 'index.html').write_text(body, encoding='utf-8')
    checksum = hashlib.sha256(body.encode('utf-8')).hexdigest()
    approval_checksum = hashlib.sha256(
        json.dumps(approval, ensure_ascii=False, sort_keys=True).encode('utf-8')
    ).hexdigest()
    (output / 'PUBLICATION_MANIFEST.json').write_text(
        json.dumps(
            {
                'schema_version': 1,
                'approval_sha256': approval_checksum,
                'route': PUBLIC_REPORT_PATH,
                'html_sha256': checksum,
                'generated_at': datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
        encoding='utf-8',
    )
    print(f'PUBLIC_EXPORT_OK pages=1 evidence=0 html_sha256={checksum} out={output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
