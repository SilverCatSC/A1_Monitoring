import json

import pytest

from app.models import MonitoringCycle
from app.service.placement_report import PlacementReportError, read_cycle_placement_report


def _cycle(cycle_id='cycle-1'):
    return MonitoringCycle(
        id=cycle_id,
        summary={'placement_reconciliation': {
            'report_path': f'cycles/{cycle_id}/placement_reconciliation.json',
        }},
    )


def _write_report(root, cycle_id='cycle-1', **changes):
    target = root / 'cycles' / cycle_id / 'placement_reconciliation.json'
    target.parent.mkdir(parents=True)
    payload = {'schema_version': 1, 'cycle_id': cycle_id,
               'findings': [], 'observed_cards': []} | changes
    target.write_text(json.dumps(payload))
    return target


def test_reader_accepts_only_report_for_its_cycle(tmp_path):
    _write_report(tmp_path)
    assert read_cycle_placement_report(_cycle(), str(tmp_path))['cycle_id'] == 'cycle-1'
    with pytest.raises(PlacementReportError, match='report_not_available'):
        read_cycle_placement_report(MonitoringCycle(id='cycle-1', summary={}), str(tmp_path))
    with pytest.raises(PlacementReportError, match='report_not_available'):
        read_cycle_placement_report(
            MonitoringCycle(id='cycle-1', summary={'placement_reconciliation': {
                'report_path': '../placement_reconciliation.json',
            }}), str(tmp_path),
        )


def test_reader_rejects_wrong_cycle_and_symlink(tmp_path):
    target = _write_report(tmp_path, cycle_id='cycle-1', findings=[{'code': 'not_verified'}])
    target.unlink()
    target.symlink_to(tmp_path / 'outside.json')
    with pytest.raises(PlacementReportError, match='invalid_report_path'):
        read_cycle_placement_report(_cycle(), str(tmp_path))


def test_reader_rejects_wrong_embedded_cycle_id(tmp_path):
    target = _write_report(tmp_path, cycle_id='cycle-1')
    payload = json.loads(target.read_text())
    payload['cycle_id'] = 'other'
    target.write_text(json.dumps(payload))
    with pytest.raises(PlacementReportError, match='invalid_report_schema'):
        read_cycle_placement_report(_cycle(), str(tmp_path))


def test_reader_rejects_malformed_card_url_shape(tmp_path):
    _write_report(tmp_path, observed_cards=[{'source': 'avito', 'url': ['not-a-url']}])
    with pytest.raises(PlacementReportError, match='invalid_report_schema'):
        read_cycle_placement_report(_cycle(), str(tmp_path))
