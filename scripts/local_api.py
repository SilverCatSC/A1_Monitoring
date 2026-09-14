"""Safe in-memory authentication for the local A1 Monitoring HTTP API.

Only loopback URLs are accepted.  The helper deliberately never accepts a
password on the command line and never sends the local Basic-auth credential to
an arbitrary URL or through a redirect.
"""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK_HOSTS = frozenset({'127.0.0.1', '::1', 'localhost'})


class LocalApiAuthenticationError(RuntimeError):
    """The local API is authenticated but no local admin credential is available."""


class _RejectRedirect(HTTPRedirectHandler):
    """Never follow a redirect while a Basic-auth header may be present."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def env_values(root: Path = ROOT) -> dict[str, str]:
    path = root / '.env'
    if not path.exists():
        return {}
    return {
        line.split('=', 1)[0]: line.split('=', 1)[1].strip().strip('\"\'')
        for line in path.read_text(encoding='utf-8-sig').splitlines()
        if '=' in line and not line.strip().startswith('#')
    }


def auth_enabled(values: dict[str, str]) -> bool:
    return values.get('AUTH_ENABLED', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}


def local_api_credentials(values: dict[str, str]) -> tuple[str, str] | None:
    """Return the local admin credentials only when local Basic-auth is enabled."""
    if not auth_enabled(values):
        return None
    username = values.get('ADMIN_USERNAME', '').strip()
    password = values.get('ADMIN_PASSWORD', '')
    if not username or not password:
        raise LocalApiAuthenticationError(
            'AUTH_ENABLED=true requires local ADMIN_USERNAME and ADMIN_PASSWORD for protected checks'
        )
    return username, password


def local_api_auth_headers(values: dict[str, str]) -> dict[str, str]:
    credentials = local_api_credentials(values)
    if credentials is None:
        return {}
    username, password = credentials
    token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
    return {'Authorization': f'Basic {token}'}


def local_api_request(url: str, *, headers: dict[str, str] | None = None) -> Request:
    """Build a request for a local API URL without allowing credential exfiltration."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError('local API URL must use a valid explicit port') from exc
    host = (parsed.hostname or '').rstrip('.').lower()
    if (
        parsed.scheme not in {'http', 'https'}
        or host not in LOOPBACK_HOSTS
        or port is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError('local API URL must use http(s) loopback with an explicit port and no URL credentials')
    return Request(url, headers=headers or {})


def open_local_api(request: Request, *, timeout: float):
    """Open a loopback request while refusing redirects to another destination."""
    local_api_request(request.full_url)
    return build_opener(_RejectRedirect()).open(request, timeout=timeout)
