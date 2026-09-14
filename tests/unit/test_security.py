import base64

import pytest

from app.security import (
    SecurityConfigurationError,
    authenticate_basic_authorization,
    configured_users,
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
    validate_security_configuration(
        environment='production',
        enabled=True,
        username='admin',
        password='long-random-password',
        network_profile='local_browser',
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


def test_local_browser_scheduler_requires_verified_host_cdp_architecture():
    base = {
        'environment': 'stage',
        'enabled': False,
        'username': None,
        'password': None,
        'network_profile': 'local_browser',
        'scheduler_enabled': True,
    }
    with pytest.raises(SecurityConfigurationError, match='HOST_CDP_SCHEDULER_VERIFIED'):
        validate_security_configuration(
            **base,
            browser_cdp_url='http://host.docker.internal:19222',
            host_cdp_scheduler_verified=False,
        )
    with pytest.raises(SecurityConfigurationError, match='non-loopback'):
        validate_security_configuration(
            **base,
            browser_cdp_url='http://127.0.0.1:19222',
            host_cdp_scheduler_verified=True,
        )
    validate_security_configuration(
        **base,
        browser_cdp_url='http://host.docker.internal:19222',
        host_cdp_scheduler_verified=True,
    )


def test_scheduler_guard_does_not_change_stage_local_vpn_behavior():
    validate_security_configuration(
        environment='stage',
        enabled=False,
        username=None,
        password=None,
        network_profile='local_vpn',
        scheduler_enabled=True,
    )


def test_configured_roles_are_server_owned_and_require_valid_accounts():
    users = configured_users(
        admin_username='admin',
        admin_password='long-random-password',
        auth_users_json=(
            '{"marketing":{"password":"marketing-secret-16","role":"marketing"},'
            '"director":{"password":"director-secret-16","role":"sales_director"}}'
        ),
    )
    assert users['admin'].role == 'admin'
    assert users['marketing'].role == 'marketing'
    actor = authenticate_basic_authorization(
        _header('director', 'director-secret-16'),
        admin_username='admin',
        admin_password='long-random-password',
        auth_users_json=(
            '[{"username":"director","password":"director-secret-16","role":"sales_director"}]'
        ),
    )
    assert actor is not None
    assert (actor.username, actor.role) == ('director', 'sales_director')
    with pytest.raises(SecurityConfigurationError, match='supported role'):
        configured_users(
            admin_username=None,
            admin_password=None,
            auth_users_json='[{"username":"unknown","password":"password","role":"owner"}]',
        )
    with pytest.raises(SecurityConfigurationError, match='supported role'):
        configured_users(
            admin_username=None,
            admin_password=None,
            auth_users_json='[{"username":"operator"}]',
        )
