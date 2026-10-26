"""Offline owner confirmation keeps observed text and source evidence intact."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Listing, MonitoringCycle
from app.service.identity_review import (
    complete_saved_identity_review,
    confirmed_operator_id,
    validate_owner_confirmation,
)
from app.service.placement_report import PlacementReportError, read_cycle_placement_report

ID = 'MBVC011220262508260005'
URL = 'https://www.avito.ru/moskva/avtomobili/mercedes_v-klass_8144322988'
CID = 'offline-review-test'


def inputs():
    row = [''] * 19
    row[0], row[3], row[4] = 'Актуально', 'Mercedes-Benz V-Class', 'V-VIP GUARD'
    row[6], row[7], row[18] = '74 450 000', '2026', URL
    snapshot = {
        'operator_authorization': 'Owner explicitly confirmed this publication for offline review',
        'marketing_row': {'values': [row]},
        'marketing_prices': {'values': [['74450000'], ['9900000']]},
        'new_feed_identities': {'values': [[ID, 'Package', '8144322988']]},
        'used_feed_identities': {'values': []},
        'feed_target_description_price': {'values': [[f'<p>ID: {ID}</p>', '74450000']]},
        'feed_target_model_year': {'values': [['Mercedes-Benz', 'V-класс', '', '', '', '', '', '', '2026']]},
    }
    report = {
        'schema_version': 1,
        'cycle_id': CID,
        'catalogue_enumeration_complete': {'avito': True},
        'catalogue_complete': {'avito': False},
        'unverified_cards_by_source': {'avito': 1},
        'findings': [
            {
                'code': 'platform_id_candidate',
                'placement_id': None,
                'suggested_placement_id': ID,
                'feed_row': 4,
                'feed_sheet': 'avito-feed-new',
                'observed_urls': [URL],
            }
        ],
        'observed_cards': [
            {
                'source': 'avito',
                'state': 'active',
                'url': URL,
                'evidence': 'card.png',
                'evidence_manifest': 'card.png.json',
                'card': {
                    'price': 74450000,
                    'year': 2026,
                    'title': 'Mercedes-Benz V-класс 2026',
                    'description_excerpt': 'V-VIP GUАRD with armour',
                    'placement_id': None,
                    'placement_id_raw': None,
                },
            }
        ],
    }
    return report, snapshot


def test_confirmation_requires_combined_evidence_not_price_alone():
    report, source = inputs()
    assert validate_owner_confirmation(report, source, ID)['placement_id'] == ID


@pytest.mark.parametrize(
    'change',
    [
        'no_owner',
        'duplicate_price',
        'other_platform',
        'duplicate_id',
        'wrong_description_id',
        'wrong_price',
        'wrong_year',
        'conflicting_card_id',
        'other_model',
        'missing_guard',
        'incomplete_catalogue',
    ],
)
def test_ambiguous_or_conflicting_confirmation_is_rejected(change):
    report, source = inputs()
    if change == 'no_owner':
        source['operator_authorization'] = ''
    elif change == 'duplicate_price':
        source['marketing_prices']['values'].append(['74450000'])
    elif change == 'other_platform':
        source['new_feed_identities']['values'][0][2] = '1234567890'
    elif change == 'duplicate_id':
        source['used_feed_identities']['values'].append([ID, '', '8144322988'])
    elif change == 'wrong_description_id':
        source['feed_target_description_price']['values'][0][0] = 'No labelled ID'
    elif change == 'wrong_price':
        report['observed_cards'][0]['card']['price'] = 9900000
    elif change == 'wrong_year':
        report['observed_cards'][0]['card']['year'] = 2025
    elif change == 'conflicting_card_id':
        report['observed_cards'][0]['card']['placement_id_raw'] = 'OTHER'
    elif change == 'other_model':
        report['observed_cards'][0]['card']['title'] = 'BMW X7 2026'
    elif change == 'missing_guard':
        report['observed_cards'][0]['card']['description_excerpt'] = 'Ordinary V-Class'
    elif change == 'incomplete_catalogue':
        report['catalogue_enumeration_complete']['avito'] = False
    with pytest.raises(ValueError):
        validate_owner_confirmation(report, source, ID)


def test_offline_review_completes_cycle_preserves_raw_files_and_reuses_only_confirmed_binding(tmp_path):
    report, source = inputs()
    folder = tmp_path / 'cycles' / CID
    (folder / 'identity_review').mkdir(parents=True)
    original = folder / 'placement_reconciliation.json'
    original.write_text(json.dumps(report))
    original_bytes = original.read_bytes()
    (folder / 'identity_review/source_snapshot.json').write_text(json.dumps(source))
    (tmp_path / 'card.png').write_bytes(b'local screenshot evidence')
    (tmp_path / 'card.png.json').write_text(
        json.dumps(
            {
                'http_status': 200,
                'screenshot_sha256': hashlib.sha256((tmp_path / 'card.png').read_bytes()).hexdigest(),
            }
        )
    )
    pending = {
        'cycle_id': CID,
        'count': 1,
        'items': [
            {
                'phase': 'identity_coverage',
                'reason': 'incomplete',
                'report_path': f'cycles/{CID}/placement_reconciliation.json',
                'source': 'avito',
            },
        ],
    }
    (folder / 'pending_checks.json').write_text(json.dumps(pending))
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Listing(id='car', vehicle_signature='fixture', is_active=True, source_avito=URL))
        db.add(
            MonitoringCycle(
                id=CID,
                status='partial',
                app_version='test',
                network_profile='local_routed_diagnostic',
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                summary={
                    'partial_reasons': ['placement_reconciliation_incomplete', 'pending_checks'],
                    'placement_reconciliation': {
                        'report_path': f'cycles/{CID}/placement_reconciliation.json'
                    },
                    'pending_checks': {'report_path': f'cycles/{CID}/pending_checks.json'},
                },
            )
        )
        db.commit()
        inspected = {'state': 'active', 'card': report['observed_cards'][0]['card']}
        assert confirmed_operator_id(db, tmp_path, URL, inspected) is None
        result = complete_saved_identity_review(db, cycle_id=CID, placement_id=ID, evidence_dir=tmp_path)
        db.commit()
        assert result['status'] == 'completed' and result['pending_checks'] == 0
        assert original.read_bytes() == original_bytes
        assert json.loads((folder / 'pending_checks.json').read_text()) == pending
        cycle = db.get(MonitoringCycle, CID)
        reviewed = read_cycle_placement_report(cycle, str(tmp_path))
        assert reviewed['catalogue_complete'] == {'avito': True}
        assert reviewed['observed_cards'][0]['card']['placement_id'] is None
        assert reviewed['observed_cards'][0]['card']['placement_id_raw'] is None
        assert reviewed['findings'][0]['code'] == 'link_current'
        assert reviewed['findings'][0]['id_match_basis'] == 'operator_confirmed'
        assert confirmed_operator_id(db, tmp_path, URL, inspected) == ID
        changed = deepcopy(inspected)
        changed['card']['price'] = 9900000
        assert confirmed_operator_id(db, tmp_path, URL, changed) is None
        changed = deepcopy(inspected)
        changed['card']['placement_id_raw'] = 'CONFLICT'
        assert confirmed_operator_id(db, tmp_path, URL, changed) is None
        original.write_text('tampered')
        with pytest.raises(PlacementReportError, match='invalid_identity_review'):
            read_cycle_placement_report(cycle, str(tmp_path))
    engine.dispose()
