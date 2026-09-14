import base64
import json

import pytest
from pydantic import ValidationError

from app.config import Settings
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


def _production_users_json(*roles: str) -> str:
    return json.dumps(
        {
            role: {
                'password': f'{role}-password-at-least-16',
                'role': role,
            }
            for role in roles
        }
    )


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
    with pytest.raises(SecurityConfigurationError, match='accepted MacBook'):
        validate_security_configuration(
            environment='production',
            enabled=True,
            username='admin',
            password='long-random-password',
            network_profile='cloud_no_vpn',
            auth_users_json=_production_users_json('operator', 'marketing', 'sales_director'),
        )
    validate_security_configuration(
        environment='production',
        enabled=True,
        username='admin',
        password='long-random-password',
        network_profile='local_browser',
        auth_users_json=_production_users_json('operator', 'marketing', 'sales_director'),
    )


@pytest.mark.parametrize('missing_role', ('operator', 'marketing', 'sales_director'))
def test_production_requires_every_m7_role(missing_role):
    roles = {'operator', 'marketing', 'sales_director'}
    roles.remove(missing_role)

    with pytest.raises(SecurityConfigurationError, match='admin/operator/marketing/sales_director'):
        validate_security_configuration(
            environment=' production ',
            enabled=True,
            username='admin',
            password='long-random-password',
            network_profile='local_browser',
            auth_users_json=_production_users_json(*sorted(roles)),
        )


def test_production_refuses_unverified_network_profile():
    with pytest.raises(SecurityConfigurationError, match='NETWORK_PROFILE'):
        validate_security_configuration(
            environment='production',
            enabled=True,
            username='admin',
            password='long-random-password',
            network_profile='local_vpn',
            auth_users_json=_production_users_json('operator', 'marketing', 'sales_director'),
        )


def test_stage_can_run_without_auth_but_is_not_production():
    validate_security_configuration(
        environment='stage', enabled=False, username=None, password=None
    )


def test_app_environment_is_normalized_and_rejects_production_policy_bypasses():
    assert Settings(APP_ENV=' Production ').app_env == 'production'
    for invalid in ('prod', 'staging', 'production-ready', ''):
        with pytest.raises(ValidationError, match='app_env must be development, stage or production'):
            Settings(APP_ENV=invalid)


def test_container_scheduler_is_rejected_even_with_a_verified_host_cdp_claim():
    base = {
        'environment': 'stage',
        'enabled': False,
        'username': None,
        'password': None,
        'network_profile': 'local_browser',
        'scheduler_enabled': True,
    }
    with pytest.raises(SecurityConfigurationError, match='SCHEDULER_ENABLED=true is not accepted'):
        validate_security_configuration(
            **base,
            browser_cdp_url='http://host.docker.internal:19222',
            host_cdp_scheduler_verified=False,
        )
    with pytest.raises(SecurityConfigurationError, match='SCHEDULER_ENABLED=true is not accepted'):
        validate_security_configuration(
            **base,
            browser_cdp_url='http://127.0.0.1:19222',
            host_cdp_scheduler_verified=True,
        )
    with pytest.raises(SecurityConfigurationError, match='SCHEDULER_ENABLED=true is not accepted'):
        validate_security_configuration(
            **base,
            browser_cdp_url='http://host.docker.internal:19222',
            host_cdp_scheduler_verified=True,
        )


def test_scheduler_guard_rejects_stage_local_vpn_too():
    with pytest.raises(SecurityConfigurationError, match='SCHEDULER_ENABLED=true is not accepted'):
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
