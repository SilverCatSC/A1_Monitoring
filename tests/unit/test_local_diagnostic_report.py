import pytest

from scripts.write_local_diagnostic_report import build_report


def test_no_vpn_report_keeps_both_marketplaces_and_cycle_isolation():
    cycle = {
        'id': '11111111-1111-4111-8111-111111111111',
        'network_profile': 'local_no_vpn',
        'status': 'partial',
        'summary': {'pending_checks': {'count': 4}, 'partial_reasons': ['pending_checks']},
    }
    scans = {
        'cycle_id': cycle['id'],
        'runs': [
            {'source': 'auto_ru', 'status': 'partial', 'filters_ok': 2,
             'filters_total': 3, 'pages_scanned': 6, 'state_counts': {'found': 1}},
            {'source': 'avito', 'status': 'partial', 'filters_ok': 0,
             'filters_total': 2, 'pages_scanned': 0, 'state_counts': {'review_required': 2}},
        ],
    }

    page = build_report(cycle, scans)

    assert 'Auto.ru' in page and 'Avito' in page
    assert '11111111-1111-4111-8111-111111111111' in page
    assert '0 / 2' in page
    with pytest.raises(ValueError, match='same cycle'):
        build_report(cycle, {**scans, 'cycle_id': 'older-cycle'})
    with pytest.raises(ValueError, match='no-VPN diagnostic'):
        build_report({**cycle, 'network_profile': 'local_browser'}, scans)
