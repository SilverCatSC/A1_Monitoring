import pytest

from scripts.build_live_agent_packet import build_packet

CYCLE_ID = 'c1111111-1111-4111-8111-111111111111'


def _cycle(status='completed'):
    return {'id': CYCLE_ID, 'status': status, 'roster_sha256': 'hash'}


def _scan(cycle_id=CYCLE_ID, status='success'):
    return {
        'cycle_id': cycle_id,
        'runs': [{'id': 'run-1', 'cycle_id': cycle_id, 'status': status}],
    }


def _audit():
    return {'cycle_id': CYCLE_ID, 'summary': {}}


def test_packet_accepts_only_one_completed_cycle():
    packet = build_packet(
        cycle_id=CYCLE_ID,
        cycle=_cycle(),
        scan=_scan(),
        head_table_audit=_audit(),
        company_site_audit=_audit(),
    )

    assert packet['schema_version'] == 2
    assert packet['cycle']['id'] == CYCLE_ID
    assert packet['contract']['cycle_rule']


@pytest.mark.parametrize(
    ('cycle', 'scan', 'head'),
    [
        (_cycle('partial'), _scan(), _audit()),
        (_cycle(), _scan(cycle_id='other-cycle'), _audit()),
        (_cycle(), _scan(), {'cycle_id': 'other-cycle'}),
    ],
)
def test_packet_rejects_partial_or_mixed_artifacts(cycle, scan, head):
    with pytest.raises(ValueError):
        build_packet(
            cycle_id=CYCLE_ID,
            cycle=cycle,
            scan=scan,
            head_table_audit=head,
            company_site_audit=_audit(),
        )
