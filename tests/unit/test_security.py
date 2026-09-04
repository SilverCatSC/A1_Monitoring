import base64

import pytest

from app.security import (
    SecurityConfigurationError,
    valid_basic_authorization,
    validate_security_configuration,
)


def _header(username: str, password: str) -> str:
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return f'Basic {token}'


def test_basic_authorization_requires_exact_credentials():
    assert valid_basic_authorization(_header('admin', 'strong-password'), 'admin', 'strong-password')
    assert not valid_basic_authorization(_header('admin', 'wrong'), 'admin', 'strong-password')
    assert not valid_basic_authorization('Bearer token', 'admin', 'strong-password')
    assert not valid_basic_authorization('Basic invalid!!!', 'admin', 'strong-password')


def test_production_refuses_disabled_or_weak_auth():
    with pytest.raises(SecurityConfigurationError):
        validate_security_configuration(
            environment='production', enabled=False, username=None, password=None
        )
    with pytest.raises(SecurityConfigurationError):
        validate_security_configuration(
            environment='production', enabled=True, username='admin', password='short'
        )
    validate_security_configuration(
        environment='production',
        enabled=True,
        username='admin',
        password='long-random-password',
        network_profile='cloud_no_vpn',
    )


def test_production_refuses_unverified_network_profile():
    with pytest.raises(SecurityConfigurationError, match='NETWORK_PROFILE'):
        validate_security_configuration(
            environment='production',
            enabled=True,
            username='admin',
            password='long-random-password',
            network_profile='local_vpn',
        )


def test_stage_can_run_without_auth_but_is_not_production():
    validate_security_configuration(
        environment='stage', enabled=False, username=None, password=None
    )
