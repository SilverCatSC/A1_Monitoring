from __future__ import annotations

import base64
import binascii
import hmac


class SecurityConfigurationError(RuntimeError):
    pass


def validate_security_configuration(
    *,
    environment: str,
    enabled: bool,
    username: str | None,
    password: str | None,
    network_profile: str = 'unknown',
) -> None:
    if environment.lower() == 'production' and (
        not enabled or not username or not password or len(password) < 16
    ):
        raise SecurityConfigurationError(
            'production requires AUTH_ENABLED=true, ADMIN_USERNAME and ADMIN_PASSWORD of 16+ characters'
        )
    if environment.lower() == 'production' and network_profile != 'cloud_no_vpn':
        raise SecurityConfigurationError(
            'production requires NETWORK_PROFILE=cloud_no_vpn after no-VPN connectivity verification'
        )


def valid_basic_authorization(
    authorization: str | None, expected_username: str | None, expected_password: str | None
) -> bool:
    if not authorization or not expected_username or not expected_password:
        return False
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'basic' or not token:
        return False
    try:
        decoded = base64.b64decode(token, validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(':')
    if not separator:
        return False
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        password, expected_password
    )
