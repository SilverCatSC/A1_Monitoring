"""Fail-closed, local-only admission record for visible-browser cycles.

The record is intentionally an operator attestation, not a route probe.  It
contains no URLs, credentials, browser state, tunnel configuration, or packet
data.  This module only reads and validates a small JSON file; it never opens
network connections, controls a browser, or changes VPN settings.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ATTESTATION_FILENAME = 'attestation.json'
OPERATIONAL_POLICY_FILENAME = 'policy.json'
SCHEMA_VERSION = 1
OPERATIONAL_SCHEMA_VERSION = 2
MAX_FILE_BYTES = 8 * 1024
MAX_VALIDITY = timedelta(hours=24)
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
REQUIRED_SERVICES = ('chatgpt', 'auto_ru', 'avito')
REQUIRED_IP_FAMILIES = ('ipv4', 'ipv6')
ALLOWED_ROUTES = frozenset({'vpn_exit', 'direct_physical_connection'})
OPERATIONAL_ROUTES = {
    'chatgpt': 'vpn_exit',
    'auto_ru': 'direct_physical_connection',
    'avito': 'direct_physical_connection',
}
SCUTIL_PATH = '/usr/sbin/scutil'
_TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$')


class VPNAdmissionError(RuntimeError):
    """A deliberately non-sensitive reason why a cycle was not admitted."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f'VPN admission rejected: {code}')


@dataclass(frozen=True)
class VPNAdmission:
    """The small non-sensitive part of a validated local attestation."""

    issued_at_utc: datetime
    expires_at_utc: datetime


@dataclass(frozen=True)
class OperationalVPNAdmission:
    """Current host readiness, not proof of marketplace egress or M7 acceptance."""

    service: str
    routes: dict[str, str]


def default_attestation_path(project_root: Path) -> Path:
    """Return the canonical owner-side path for a local host run."""
    return project_root / 'artifacts' / 'vpn_admission' / ATTESTATION_FILENAME


def default_operational_policy_path(project_root: Path) -> Path:
    return project_root / 'artifacts' / 'vpn_admission' / OPERATIONAL_POLICY_FILENAME


def require_operational_vpn_admission(
    path: str | Path, *, command_runner=None, platform: str | None = None,
) -> OperationalVPNAdmission:
    """Require the owner's stable route policy and a connected VPSUS service.

    This intentionally does not claim to verify per-domain IPv4/IPv6 egress.
    Full M7 route acceptance remains a separate evidence-based decision.
    """
    if (platform or sys.platform) != 'darwin':
        raise VPNAdmissionError('macos_required')
    policy_path = Path(path)
    _require_private_directory(policy_path.parent)
    payload = _read_json(policy_path)
    if set(payload) != {'schema_version', 'status', 'services'}:
        raise VPNAdmissionError('unexpected_operational_policy_schema')
    if payload['schema_version'] != OPERATIONAL_SCHEMA_VERSION or payload['status'] != 'approved':
        raise VPNAdmissionError('operational_policy_unapproved')
    services = payload['services']
    if not isinstance(services, dict) or set(services) != set(OPERATIONAL_ROUTES):
        raise VPNAdmissionError('operational_policy_services_invalid')
    if any(
        not isinstance(services[name], dict)
        or services[name] != {'route': expected}
        for name, expected in OPERATIONAL_ROUTES.items()
    ):
        raise VPNAdmissionError('operational_policy_routes_invalid')

    run = command_runner or subprocess.run
    try:
        listed = run(
            [SCUTIL_PATH, '--nc', 'list'],
            capture_output=True, text=True, timeout=5, check=False,
        )
        status = run(
            [SCUTIL_PATH, '--nc', 'status', 'VPSUS'],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VPNAdmissionError('vpn_status_unavailable') from exc
    if listed.returncode != 0 or status.returncode != 0:
        raise VPNAdmissionError('vpn_status_unavailable')
    matching = [
        line for line in listed.stdout.splitlines()
        if 'VPN (com.vpsus.vpsus) "VPSUS"' in line and '[VPN:com.vpsus.vpsus]' in line
    ]
    if (len(matching) != 1 or not matching[0].lstrip().startswith('* (Connected)')
            or status.stdout.splitlines()[:1] != ['Connected']):
        raise VPNAdmissionError('vpsus_not_connected')
    return OperationalVPNAdmission('VPSUS', dict(OPERATIONAL_ROUTES))


def prepare_attestation_directory(path: str | Path) -> None:
    """Create one owner-only admission directory without creating an approval.

    This is deliberately separate from :func:`require_vpn_admission`: reading
    an attestation is side-effect free. Interactive host entry points may use
    this helper before their service work so the admission location is always
    a dedicated owner-private runtime path.
    """
    directory = Path(path)
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError:
        pass
    except OSError as exc:
        raise VPNAdmissionError('directory_unavailable') from exc
    _require_private_directory(directory)


def require_vpn_admission(
    path: str | Path,
    *,
    now: datetime | None = None,
) -> VPNAdmission:
    """Read one strict, fresh local admission record or raise fail-closed.

    Expected JSON shape::

        {
          "schema_version": 1,
          "status": "approved",
          "issued_at_utc": "2026-09-14T12:00:00Z",
          "expires_at_utc": "2026-09-15T12:00:00Z",
          "services": {
            "chatgpt": {"route": "vpn_exit", "ipv4": "verified", "ipv6": "verified"},
            "auto_ru": {"route": "direct_physical_connection", "ipv4": "verified", "ipv6": "verified"},
            "avito": {"route": "direct_physical_connection", "ipv4": "verified", "ipv6": "verified"}
          }
        }

    The strings are intentionally constrained rather than free-form so the
    file cannot become an accidental store of URLs, screenshots, usernames,
    tokens, or VPN configuration exports.
    """
    record_path = Path(path)
    _require_private_directory(record_path.parent)
    payload = _read_json(record_path)
    issued_at = _validate_payload(payload)
    expires_at = _parse_utc_timestamp(payload['expires_at_utc'])
    current = _normalize_now(now)

    if issued_at > current + MAX_FUTURE_CLOCK_SKEW:
        raise VPNAdmissionError('issued_in_future')
    if expires_at <= current:
        raise VPNAdmissionError('expired')
    if expires_at <= issued_at:
        raise VPNAdmissionError('invalid_validity_window')
    if expires_at - issued_at > MAX_VALIDITY:
        raise VPNAdmissionError('validity_window_too_long')
    return VPNAdmission(issued_at_utc=issued_at, expires_at_utc=expires_at)


def _read_json(path: Path) -> dict[str, Any]:
    """Read a bounded regular file without following a symlink where supported."""
    try:
        initial_stat = path.lstat()
    except FileNotFoundError as exc:
        raise VPNAdmissionError('missing') from exc
    except OSError as exc:
        raise VPNAdmissionError('unreadable') from exc
    if stat.S_ISLNK(initial_stat.st_mode):
        raise VPNAdmissionError('symlink_not_allowed')
    if not stat.S_ISREG(initial_stat.st_mode):
        raise VPNAdmissionError('regular_file_required')
    _require_private_permissions(initial_stat, path)

    flags = os.O_RDONLY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise VPNAdmissionError('missing') from exc
    except OSError as exc:
        raise VPNAdmissionError('unreadable') from exc

    try:
        with os.fdopen(descriptor, 'rb', closefd=True) as handle:
            opened_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened_stat.st_mode):
                raise VPNAdmissionError('regular_file_required')
            _require_private_permissions(opened_stat, path)
            if (opened_stat.st_dev, opened_stat.st_ino) != (initial_stat.st_dev, initial_stat.st_ino):
                raise VPNAdmissionError('file_changed_during_read')
            raw = handle.read(MAX_FILE_BYTES + 1)
    except VPNAdmissionError:
        raise
    except OSError as exc:
        raise VPNAdmissionError('unreadable') from exc

    if len(raw) > MAX_FILE_BYTES:
        raise VPNAdmissionError('too_large')
    try:
        parsed = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VPNAdmissionError('invalid_json') from exc
    if not isinstance(parsed, dict):
        raise VPNAdmissionError('object_required')
    return parsed


def _require_private_permissions(file_stat: os.stat_result, path: Path) -> None:
    """Reject group/world-accessible attestation files on POSIX hosts.

    Windows ACL inspection belongs to the Windows fallback runtime rather than
    this portable JSON parser, so its POSIX mode bits are not treated as an ACL
    assertion there.
    """
    if os.name != 'posix':
        return
    if file_stat.st_uid != os.geteuid():
        raise VPNAdmissionError('file_owner_mismatch')
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise VPNAdmissionError('insecure_permissions')
    _require_no_extended_macos_acl(path)


def _require_private_directory(directory: Path) -> None:
    """Require the same owner-only directory contract during every read."""
    try:
        directory_stat = directory.lstat()
    except OSError as exc:
        raise VPNAdmissionError('directory_unavailable') from exc
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
        raise VPNAdmissionError('private_directory_required')
    if os.name == 'posix':
        if directory_stat.st_uid != os.geteuid():
            raise VPNAdmissionError('directory_owner_mismatch')
        if stat.S_IMODE(directory_stat.st_mode) != 0o700:
            raise VPNAdmissionError('insecure_directory_permissions')
        _require_no_extended_macos_acl(directory)


def _require_no_extended_macos_acl(path: Path) -> None:
    """Fail closed for macOS extended ACLs that mode bits do not describe."""
    if sys.platform != 'darwin':
        return
    try:
        result = subprocess.run(
            ['/bin/ls', '-lde', str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VPNAdmissionError('acl_inspection_unavailable') from exc
    if result.returncode != 0 or _macos_extended_acl_present(result.stdout):
        raise VPNAdmissionError('extended_acl_not_allowed')


def _macos_extended_acl_present(ls_output: str) -> bool:
    """Return whether macOS ``ls -le`` marks the displayed item with ``+``."""
    first_line = next((line.strip() for line in ls_output.splitlines() if line.strip()), '')
    mode_token = first_line.split(maxsplit=1)[0] if first_line else ''
    return not mode_token or mode_token.endswith('+')


def _validate_payload(payload: dict[str, Any]) -> datetime:
    expected_top_level = {
        'schema_version',
        'status',
        'issued_at_utc',
        'expires_at_utc',
        'services',
    }
    if set(payload) != expected_top_level:
        raise VPNAdmissionError('unexpected_schema')
    if payload['schema_version'] != SCHEMA_VERSION or payload['status'] != 'approved':
        raise VPNAdmissionError('unapproved')
    issued_at = _parse_utc_timestamp(payload['issued_at_utc'])
    services = payload['services']
    if not isinstance(services, dict) or set(services) != set(REQUIRED_SERVICES):
        raise VPNAdmissionError('required_services_missing')
    for service in REQUIRED_SERVICES:
        details = services[service]
        if not isinstance(details, dict) or set(details) != {
            'route', *REQUIRED_IP_FAMILIES,
        }:
            raise VPNAdmissionError('invalid_service_attestation')
        if details['route'] not in ALLOWED_ROUTES:
            raise VPNAdmissionError('invalid_route')
        if any(details[family] != 'verified' for family in REQUIRED_IP_FAMILIES):
            raise VPNAdmissionError('ip_family_not_verified')
    return issued_at


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise VPNAdmissionError('invalid_timestamp')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise VPNAdmissionError('invalid_timestamp') from exc
    return parsed.astimezone(UTC)


def _normalize_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    return value.astimezone(UTC)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Read or safely prepare the local VPN admission boundary.'
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--path', help='Read one existing attestation JSON file.')
    action.add_argument(
        '--operational-policy',
        help='Check the owner policy and current VPSUS connection without claiming route acceptance.',
    )
    action.add_argument(
        '--prepare-directory',
        help='Create or verify one empty owner-only directory before a host run.',
    )
    args = parser.parse_args(argv)
    try:
        if args.prepare_directory:
            prepare_attestation_directory(args.prepare_directory)
            print('VPN_ADMISSION_DIRECTORY_READY')
            return 0
        if args.operational_policy:
            require_operational_vpn_admission(args.operational_policy)
            print('VPN_OPERATIONAL_ADMISSION_OK vpn=connected routes=declared egress=unverified')
            return 0
        admission = require_vpn_admission(args.path)
    except VPNAdmissionError as exc:
        print(f'VPN_ADMISSION_REFUSED code={exc.code}', file=sys.stderr)
        return 2
    expiry = admission.expires_at_utc.isoformat().replace('+00:00', 'Z')
    print(f'VPN_ADMISSION_OK expires_at_utc={expiry}')
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
