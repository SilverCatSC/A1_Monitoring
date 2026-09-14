from __future__ import annotations

import base64
import binascii
import hmac
import json
from dataclasses import dataclass

from app.config import is_non_loopback_cdp_url


class SecurityConfigurationError(RuntimeError):
    pass


ROLES = frozenset({'admin', 'operator', 'marketing', 'sales_director'})
REQUIRED_PRODUCTION_ROLES = ROLES


@dataclass(frozen=True)
class AuthenticatedActor:
    username: str
    role: str


@dataclass(frozen=True)
class ConfiguredUser:
    username: str
    password: str
    role: str


def configured_users(
    *,
    admin_username: str | None,
    admin_password: str | None,
    auth_users_json: str | None = None,
) -> dict[str, ConfiguredUser]:
    """Parse credentials from environment without ever logging the passwords.

    `ADMIN_*` remains a backwards-compatible admin account. `AUTH_USERS_JSON`
    adds accounts as either a list of `{username,password,role}` objects or a
    mapping from username to `{password,role}`. It belongs only in secret-backed
    environment configuration, never in Git.
    """
    entries: list[dict] = []
    if bool(admin_username) != bool(admin_password):
        raise SecurityConfigurationError('ADMIN_USERNAME and ADMIN_PASSWORD must be configured together')
    if admin_username and admin_password:
        entries.append({'username': admin_username, 'password': admin_password, 'role': 'admin'})
    if auth_users_json:
        try:
            parsed = json.loads(auth_users_json)
        except json.JSONDecodeError as exc:
            raise SecurityConfigurationError('AUTH_USERS_JSON must be valid JSON') from exc
        if isinstance(parsed, dict):
            if any(not isinstance(value, dict) for value in parsed.values()):
                raise SecurityConfigurationError('AUTH_USERS_JSON account values must be objects')
            parsed = [dict(value, username=username) for username, value in parsed.items()]
        if not isinstance(parsed, list):
            raise SecurityConfigurationError('AUTH_USERS_JSON must be an object or a list')
        if any(not isinstance(item, dict) for item in parsed):
            raise SecurityConfigurationError('AUTH_USERS_JSON contains an invalid account')
        entries.extend(parsed)

    users: dict[str, ConfiguredUser] = {}
    for entry in entries:
        username = str(entry.get('username') or '').strip()
        password = str(entry.get('password') or '')
        role = str(entry.get('role') or '').strip().lower()
        if not username or not password or role not in ROLES or username in users:
            raise SecurityConfigurationError('each configured user needs a unique username, password and supported role')
        users[username] = ConfiguredUser(username=username, password=password, role=role)
    return users


def authenticate_basic_authorization(
    authorization: str | None,
    *,
    admin_username: str | None,
    admin_password: str | None,
    auth_users_json: str | None = None,
) -> AuthenticatedActor | None:
    """Authenticate against configured users and return the server-owned role."""
    credentials = _decode_basic_authorization(authorization)
    if credentials is None:
        return None
    username, password = credentials
    users = configured_users(
        admin_username=admin_username,
        admin_password=admin_password,
        auth_users_json=auth_users_json,
    )
    actor = users.get(username)
    if actor is None:
        return None
    if not hmac.compare_digest(password, actor.password):
        return None
    return AuthenticatedActor(username=actor.username, role=actor.role)


def validate_security_configuration(
    *,
    environment: str,
    enabled: bool,
    username: str | None,
    password: str | None,
    network_profile: str = 'unknown',
    auth_users_json: str | None = None,
    scheduler_enabled: bool = False,
    browser_cdp_url: str | None = None,
    host_cdp_scheduler_verified: bool = False,
) -> None:
    environment = environment.lower().strip()
    if environment not in {'development', 'stage', 'production'}:
        raise SecurityConfigurationError('environment must be development, stage or production')
    network_profile = network_profile.lower().strip()
    users = configured_users(
        admin_username=username,
        admin_password=password,
        auth_users_json=auth_users_json,
    )
    if environment == 'production' and (
        not enabled
        or not users
        or not REQUIRED_PRODUCTION_ROLES.issubset({actor.role for actor in users.values()})
        or any(len(actor.password) < 16 for actor in users.values())
    ):
        raise SecurityConfigurationError(
            'production requires AUTH_ENABLED=true, admin/operator/marketing/sales_director roles, '
            'and 16+ character passwords for all users'
        )
    if environment == 'production' and network_profile not in {
        'local_browser',
        'cloud_no_vpn',
    }:
        raise SecurityConfigurationError(
            'production requires NETWORK_PROFILE=local_browser or cloud_no_vpn '
            'after connectivity verification'
        )
    if (
        scheduler_enabled
        and network_profile.lower().strip() == 'local_browser'
        and (
            not host_cdp_scheduler_verified
            or not is_non_loopback_cdp_url(browser_cdp_url)
        )
    ):
        raise SecurityConfigurationError(
            'SCHEDULER_ENABLED=true with NETWORK_PROFILE=local_browser requires '
            'HOST_CDP_SCHEDULER_VERIFIED=true and a non-loopback BROWSER_CDP_URL '
            'with an explicit port'
        )


def valid_basic_authorization(
    authorization: str | None, expected_username: str | None, expected_password: str | None
) -> bool:
    if not expected_username or not expected_password:
        return False
    credentials = _decode_basic_authorization(authorization)
    if credentials is None:
        return False
    username, password = credentials
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        password, expected_password
    )


def _decode_basic_authorization(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'basic' or not token:
        return None
    try:
        decoded = base64.b64decode(token, validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(':')
    return (username, password) if separator else None
