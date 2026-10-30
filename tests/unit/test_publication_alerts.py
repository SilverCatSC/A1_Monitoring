import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, MonitoringCycle
from app.scraper.base import evidence_manifest_name
from app.service.publication_alerts import add_publication_alerts, publication_alerts

UID = 'MBVC011220262508260009'
OTHER = 'MBVC011220262508260008'
OLD = 'https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/1133334801-a/'
NEW = OLD.replace('1133334801-a', '1133334802-b')


def card(url=OLD, uid=UID, state='active', source='auto_ru'):
    return {
        'source': source,
        'url': url,
        'state': state,
        'evidence': 'card.png',
        'evidence_manifest': evidence_manifest_name('card.png'),
        'card': {'placement_id': uid, 'vin': 'SAMEVIN', 'price': 39990000},
    }


def report(cards, complete=True, cycle='current'):
    return {
        'schema_version': 1,
        'cycle_id': cycle,
        'findings': [],
        'observed_cards': cards,
        'catalogue_enumeration_complete': {'auto_ru': complete},
        'catalogue_complete': {'auto_ru': complete},
    }


def changes(cards, complete=True):
    return publication_alerts(report(cards, complete), {'auto_ru': report([card()], cycle='previous')})


@pytest.mark.parametrize(
    'complete,kind,certainty',
    [(True, 'republished', 'confirmed'), (False, 'republication_candidate', 'old_state_unproven')],
)
def test_new_url_requires_exact_unique_id(complete, kind, certainty):
    result = changes([card(NEW)], complete)
    assert len(result['items']) == 1
    alert = result['items'][0]
    assert (alert['kind'], alert['certainty']) == (kind, certainty)
    assert (alert['old_url'], alert['new_url'], alert['unique_id']) == (OLD, NEW, UID)
    assert not alert['vehicle_sale_confirmed'] and result['source_writes'] is False


def test_explicit_old_removal_confirms_republication_with_partial_catalogue():
    result = changes([card(state='removed'), card(NEW)], False)
    assert result['counts'] == {'republished': 1}


@pytest.mark.parametrize('uid', [None, OTHER, 'МBVC011220262508260009'])
def test_same_vin_and_price_or_visual_alias_does_not_bind_new_publication(uid):
    current = card(NEW, uid)
    current['unique_id'] = UID  # A proposed feed/alias match is not an exact new card claim.
    result = changes([current])
    assert not any(a['kind'].startswith('repub') for a in result['items'])
    assert result['counts']['publication_disappeared'] == 1


def test_two_active_urls_for_same_id_are_duplicate_not_republication():
    result = changes([card(), card(NEW)])
    assert result['counts'] == {'duplicate_unique_id': 1}
    assert len(result['items'][0]['urls']) == 2


def test_partial_catalogue_warns_but_does_not_claim_disappearance():
    result = changes([], False)
    assert result['counts'] == {'inventory_drop': 1, 'publication_unverified': 1}
    assert all(a['certainty'] == 'coverage_unproven' for a in result['items'])


@pytest.mark.parametrize('condition', ['inspection_partial', 'skipped', 'blocked', 'missing_proof', 'listed'])
def test_uninspected_or_listed_previous_url_cannot_disappear(condition):
    r = report([])
    if condition == 'inspection_partial':
        r['catalogue_complete']['auto_ru'] = False
    elif condition == 'skipped':
        r['skipped_cards_by_source'] = {'auto_ru': 1}
    elif condition == 'blocked':
        r['blocked_sources'] = ['auto_ru']
    elif condition == 'missing_proof':
        r.pop('catalogue_complete')
    else:
        r['catalogue_listing_urls'] = {'auto_ru': [OLD]}
    result = publication_alerts(r, {'auto_ru': report([card()], cycle='previous')})
    assert any(a['kind'] == 'publication_unverified' for a in result['items'])
    assert not any(a['kind'] == 'publication_disappeared' for a in result['items'])


def test_saved_false_absence_is_downgraded_without_changing_original():
    from app.service.publication_alerts import apply_absence_caveats
    original = report([])
    original['publication_alerts'] = changes([])
    original['catalogue_complete']['auto_ru'] = False
    before = copy.deepcopy(original)
    view = copy.deepcopy(original)
    apply_absence_caveats(view)
    assert original == before
    assert view['publication_alerts']['counts']['publication_unverified'] == 1
    assert 'publication_disappeared' not in view['publication_alerts']['counts']
    assert view['coverage_reassessment']['source_evidence_unchanged']


def test_complete_catalogue_and_explicit_removed_have_different_proof():
    complete = changes([])
    explicit = changes([card(state='removed')], False)
    for r, basis in [(complete, 'absent_from_complete_seller_catalogue'), (explicit, 'explicit_removal')]:
        alert = next(a for a in r['items'] if a['kind'] == 'publication_disappeared')
        assert alert['certainty'] == 'confirmed' and alert['basis'] == basis


def test_unknown_direct_result_is_not_explicit_removal():
    result = changes([card(state='unknown')], False)
    assert 'publication_disappeared' not in result['counts']


def test_no_baseline_reports_explicit_inactive_without_inventing_previous_counts():
    result = publication_alerts(report([card(state='removed')], False))
    assert result['counts'] == {'publication_inactive': 1}
    assert result['baseline_cycle_by_source']['auto_ru'] is None


def test_cars_and_commercial_counts_are_separate_and_stable():
    lcv = card('https://auto.ru/lcv/used/sale/mercedes/sprinter/1126771072-a/', None)
    previous = report([card(), lcv], cycle='previous')
    current = report([lcv])
    result = publication_alerts(current, {'auto_ru': previous})
    drop = next(a for a in result['items'] if a['kind'] == 'inventory_drop')
    assert (drop['segment'], drop['previous_count'], drop['observed_count']) == ('cars', 1, 0)
    assert result == publication_alerts(copy.deepcopy(current), {'auto_ru': previous})


def test_missing_evidence_and_other_marketplace_cannot_supply_republication():
    bad = card(NEW)
    bad['evidence_manifest'] = 'wrong.json'
    avito = card('https://www.avito.ru/moskva/avtomobili/mercedes-benz_8271920707', source='avito')
    result = changes([bad, avito], False)
    assert not any(a['kind'].startswith('repub') for a in result['items'])


def test_restored_publication_has_own_signal():
    result = publication_alerts(
        report([card()]), {'auto_ru': report([card(state='removed')], cycle='previous')}
    )
    assert result['counts'] == {'publication_restored': 1}


def test_history_uses_valid_earlier_report_and_does_not_modify_it(tmp_path):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)

    def cycle(cid, days):
        return MonitoringCycle(
            id=cid,
            status='partial',
            network_profile='local_browser',
            app_version='test',
            started_at=now - timedelta(days=days),
            finished_at=now - timedelta(days=days, hours=-1),
            summary={
                'placement_reconciliation': {'report_path': f'cycles/{cid}/placement_reconciliation.json'}
            },
        )

    path = tmp_path / 'cycles/previous/placement_reconciliation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report([card()], cycle='previous')))
    before = path.read_bytes()
    with Session(engine) as db:
        db.add_all([cycle('current', 0), cycle('broken', 1), cycle('previous', 2), cycle('future', -1)])
        db.commit()
        current = report([card(NEW)])
        alerts = add_publication_alerts(db, current, tmp_path)
        assert alerts['baseline_cycle_by_source']['auto_ru'] == 'previous'
        assert alerts['counts'] == {'republished': 1}
        assert path.read_bytes() == before


def test_dashboard_renders_alerts_and_escapes_text():
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    from app.service.monitoring_digest import monitoring_digest
    from app.service.vehicle_dashboard import vehicle_dashboard

    templates = Path(__file__).resolve().parents[2] / 'src/app/templates'
    env = Environment(
        loader=ChoiceLoader(
            [DictLoader({'base.html': '{% block content %}{% endblock %}'}), FileSystemLoader(str(templates))]
        ),
        autoescape=True,
    )
    r = report([])
    r.update(created_at_utc='2026-10-20', catalogue_complete={}, new_card_checks=0)
    r['publication_alerts'] = changes([])
    r['publication_alerts']['items'][0]['title'] = '<script>bad</script>'
    output = env.get_template('placement_identity.html').render(
        data={
            'cycle': {'id': 'current', 'status': 'partial'},
            'report': r,
            'review': [],
            'available_cycles': [],
            'monitoring': monitoring_digest(r),
            'vehicles': vehicle_dashboard(r),
        }
    )
    assert 'Сигналы об изменении публикаций' in output and 'Легковые автомобили' in output
    assert '&lt;script&gt;' in output and '<script>bad</script>' not in output


@pytest.mark.parametrize('missing_evidence', [False, True])
def test_complete_catalogue_does_not_turn_failed_direct_check_into_disappearance(missing_evidence):
    old = card(state='unknown')
    if missing_evidence:
        old.pop('evidence_manifest')
    result = changes([old], True)
    assert 'publication_disappeared' not in result['counts']
    assert result['counts']['publication_unverified'] == 1
    assert all(a['certainty'] != 'confirmed' for a in result['items'])


def test_unknown_old_card_keeps_new_exact_id_link_a_candidate():
    result = changes([card(state='unknown'), card(NEW)], True)
    assert result['counts'] == {'republication_candidate': 1}


def test_open_disappearance_and_drop_survive_unchanged_partial_cycles():
    first = report([], False, cycle='first')
    # First check has a complete catalogue; subsequent checks cannot reproduce it.
    first['catalogue_enumeration_complete']['auto_ru'] = True
    first['publication_alerts'] = publication_alerts(first, {'auto_ru': report([card()], cycle='initial')})
    prior = first
    for cid in ['second', 'third']:
        current = report([], False, cycle=cid)
        current['publication_alerts'] = publication_alerts(current, {'auto_ru': prior})
        alerts = current['publication_alerts']
        assert alerts['counts'] == {'inventory_drop': 1, 'publication_unverified': 1}
        assert all(a['ongoing'] and a['first_detected_cycle_id'] == 'first' for a in alerts['items'])
        assert all(a['certainty'] == 'coverage_unproven' for a in alerts['items'])
        prior = current


def test_open_warning_resolves_when_same_publication_returns():
    previous = report([], cycle='missing')
    previous['publication_alerts'] = changes([])
    result = publication_alerts(report([card()]), {'auto_ru': previous})
    assert result['counts'] == {'publication_restored': 1}


@pytest.mark.parametrize('kind', ['publication_unverified', 'republication_candidate', 'publication_disappeared'])
def test_unproven_prior_absence_only_confirms_current_activity(kind):
    previous = report([], False, cycle='blocked')
    previous['publication_alerts'] = {'items': [{
        'kind': kind, 'source': 'auto_ru', 'old_url': OLD, 'unique_id': UID,
        'certainty': 'coverage_unproven',
    }]}
    result = publication_alerts(report([card()]), {'auto_ru': previous})
    assert result['counts'] == {'publication_verified': 1}
    assert result['items'][0]['basis'] == 'previous_state_unverified'


def test_saved_restoration_without_prior_proof_is_relabelled_only_in_view():
    from app.service.publication_alerts import apply_absence_caveats
    original = report([card()])
    original['publication_alerts'] = {'items': [{
        'kind': 'publication_restored', 'source': 'auto_ru', 'new_url': OLD,
        'certainty': 'confirmed',
    }]}
    view = copy.deepcopy(original)
    apply_absence_caveats(view)
    assert original['publication_alerts']['items'][0]['kind'] == 'publication_restored'
    assert view['publication_alerts']['counts'] == {'publication_verified': 1}
    assert view['coverage_reassessment']['changed_alerts'] == 1


def test_explicitly_proven_restoration_survives_saved_readback():
    from app.service.publication_alerts import apply_absence_caveats
    current = report([card()])
    current['publication_alerts'] = publication_alerts(
        current, {'auto_ru': report([card(state='removed')], False, cycle='removed')})
    apply_absence_caveats(current)
    assert current['publication_alerts']['counts'] == {'publication_restored': 1}


def test_open_warning_finds_new_exact_id_url_without_old_card_in_latest_report():
    previous = report([], cycle='missing')
    previous['publication_alerts'] = changes([])
    current = report([card(NEW)], False, cycle='new-found')
    current['publication_alerts'] = publication_alerts(current, {'auto_ru': previous})
    assert current['publication_alerts']['counts'] == {'republication_candidate': 1}
    next_check = report([card(NEW)], False, cycle='still-unproven')
    result = publication_alerts(next_check, {'auto_ru': current})
    assert result['counts'] == {'republication_candidate': 1}
    assert result['items'][0]['old_url'] == OLD and result['items'][0]['new_url'] == NEW


def test_ongoing_drop_keeps_initial_count_when_it_gets_worse():
    older = report([card(), card(NEW, OTHER)], cycle='initial')
    first = report([card()], cycle='first')
    first['publication_alerts'] = publication_alerts(first, {'auto_ru': older})
    final = publication_alerts(report([], cycle='final'), {'auto_ru': first})
    drop = next(a for a in final['items'] if a['kind'] == 'inventory_drop')
    assert (drop['previous_count'], drop['observed_count'], drop['reference_cycle_id']) == (2, 0, 'initial')


def test_history_keeps_alert_only_empty_catalogue_as_latest_baseline(tmp_path):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    prior = report([], False, cycle='missing')
    prior['publication_alerts'] = publication_alerts(prior, {'auto_ru': report([card()], cycle='initial')})
    path = tmp_path / 'cycles/missing/placement_reconciliation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(prior))
    with Session(engine) as db:
        for cid, days in [('missing', 1), ('current', 0)]:
            db.add(
                MonitoringCycle(
                    id=cid,
                    status='partial',
                    network_profile='local_browser',
                    app_version='test',
                    started_at=now - timedelta(days=days),
                    finished_at=now - timedelta(days=days, hours=-1),
                    summary={
                        'placement_reconciliation': {
                            'report_path': f'cycles/{cid}/placement_reconciliation.json'
                        }
                    },
                )
            )
        db.commit()
        current = report([], False)
        alerts = add_publication_alerts(db, current, tmp_path)
        assert alerts['baseline_cycle_by_source']['auto_ru'] == 'missing'
        assert alerts['counts'] == {'inventory_drop': 1, 'publication_unverified': 1}


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('state,uid', [('removed', UID), ('active', OTHER)])
def test_conflicting_duplicate_url_cannot_prove_disappearance_or_republication(reverse, state, uid):
    duplicates = [card(), card(state=state, uid=uid)]
    if reverse:
        duplicates.reverse()
    result = changes(duplicates)
    assert result['counts']['publication_unverified'] == 1
    assert not any(
        a['kind'] in {'publication_disappeared', 'republished', 'publication_inactive'}
        for a in result['items']
    )
    assert all(a['certainty'] == 'coverage_unproven' for a in result['items'])


def test_identical_duplicate_url_is_one_observation_not_inventory_change():
    assert changes([card(), card()])['total'] == 0
