import json
from pathlib import Path

import pytest

from app.service import monitoring_history as history


def obs(day, *, uid='MBVC022220202508260022', key='auto_ru:123', source='auto_ru', state='active', cycle='a'):
    return {
        'key': key,
        'source': source,
        'unique_id': uid,
        'at': f'2026-10-{day:02}T10:00:00+00:00',
        'state': state,
        'title': 'V-Class',
        'url': 'https://auto.ru/cars/used/sale/mercedes/v_klasse/123-a/',
        'price': 10500000,
        'cycle_id': cycle,
    }


def test_reused_evidence_and_same_day_do_not_inflate_days():
    a = obs(1)
    result = history.summarize_observations(
        [a, {**a, 'cycle_id': 'b'}, {**a, 'at': '2026-10-01T18:00:00+00:00'}]
    )
    row = result['rows'][0]
    assert row['active_days'] == 1 and len(row['events']) == 2


def test_gap_is_not_counted_as_active_and_removed_is_not_active():
    row = history.summarize_observations([obs(1), obs(2, state='removed'), obs(10)])['rows'][0]
    assert row['active_days'] == 2 and row['span_days'] == 10 and row['unconfirmed_days'] == 8
    assert row['first_active'] == '2026-10-01' and row['last_active'] == '2026-10-10'


def test_same_id_republication_joins_but_platforms_remain_separate():
    result = history.summarize_observations(
        [obs(1), obs(2, key='auto_ru:456'), obs(3, key='avito:123', source='avito')]
    )
    assert len(result['rows']) == 2
    auto = next(r for r in result['rows'] if r['source'] == 'auto_ru')
    assert auto['active_days'] == 2 and auto['publications'] == 2


def test_equal_prices_and_titles_never_join_unknown_ids():
    result = history.summarize_observations([obs(1, uid=None), obs(2, uid=None, key='auto_ru:456')])
    assert len(result['rows']) == 2


def test_missing_id_only_inherits_from_same_publication():
    row = history.summarize_observations([obs(1, uid=None), obs(2)])['rows'][0]
    assert row['active_days'] == 2 and row['unique_id'] == 'MBVC022220202508260022'


def test_conflicting_id_stays_publication_scoped():
    result = history.summarize_observations(
        [obs(1), obs(2, uid='MBVC022220212508260023'), obs(3, key='auto_ru:456')]
    )
    assert len(result['rows']) == 2
    assert result['by_url']['auto_ru:123']['unique_id'] is None


def test_utc_date_is_used_and_naive_timestamps_rejected():
    row = history.summarize_observations([obs(1), {**obs(2), 'at': '2026-10-02T01:00:00+03:00'}])['rows'][0]
    assert row['active_days'] == 1
    with pytest.raises(ValueError):
        history.timestamp('2026-10-02T01:00:00')


def report_fixture(root, monkeypatch):
    cid = '230c09ee-b8b6-4c65-93e1-3fb557964c7e'
    folder = root / 'cycles' / cid
    folder.mkdir(parents=True)
    image = root / 'card.png'
    image.write_bytes(b'image')
    (root / 'card.png.json').write_text('{}')
    monkeypatch.setattr(history, '_proof', lambda *a: {'captured_at': '2026-10-01T10:00:00+00:00'})
    report = {
        'schema_version': 1,
        'cycle_id': cid,
        'created_at_utc': '2026-10-02T12:00:00+00:00',
        'observed_cards': [
            {
                'source': 'auto_ru',
                'state': 'active',
                'url': obs(1)['url'],
                'evidence': 'card.png',
                'card': {'placement_id': obs(1)['unique_id'], 'title': 'V-Class'},
            }
        ],
    }
    path = folder / 'placement_reconciliation.json'
    path.write_text(json.dumps(report))
    return path, report


def test_loader_uses_capture_time_not_resumed_cycle_time(tmp_path, monkeypatch):
    path, report = report_fixture(tmp_path, monkeypatch)
    result = history.load_history(tmp_path, until=report['created_at_utc'])
    assert result['rows'][0]['last_active'] == '2026-10-01'
    assert len(result['reports']) == 1
    assert not history.load_history(tmp_path, until='2026-10-01T12:00:00+00:00')['rows']


def test_invalid_evidence_does_not_create_activity(tmp_path, monkeypatch):
    report_fixture(tmp_path, monkeypatch)

    def bad(*a):
        raise ValueError('bad evidence')

    monkeypatch.setattr(history, '_proof', bad)
    result = history.load_history(tmp_path)
    assert result['rows'] == [] and result['excluded_records'] == 1


def test_wrong_cycle_or_tampered_bundle_rejected(tmp_path, monkeypatch):
    path, report = report_fixture(tmp_path, monkeypatch)
    report['artifact_bundle'] = 'report_bundle.ready.json'
    path.write_text(json.dumps(report))
    assert history.load_history(tmp_path)['reports'] == []


def test_history_template_escapes_titles():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    root = Path(__file__).resolve().parents[2]
    data = history.summarize_observations([{**obs(1), 'title': '<script>bad()</script>'}])
    data.update(as_of='2026-10-02', reports=[], excluded_records=0)
    html = (
        Environment(
            loader=FileSystemLoader(root / 'src/app/templates'), autoescape=select_autoescape(['html'])
        )
        .get_template('monitoring_history.html')
        .render(history=data, latest_url='index.html')
    )
    assert '<script>bad()</script>' not in html and '&lt;script&gt;' in html


def test_conflicting_simultaneous_observation_does_not_count_active():
    result = history.summarize_observations([obs(1), obs(1, state='removed')])
    assert result['rows'] == []


@pytest.mark.parametrize('url', ['https://user:secret@auto.ru/cars/12345/',
                                 'https://auto.ru:8443/cars/12345/',
                                 'https://evil.test/cars/12345/',
                                 'javascript:alert(1)'])
def test_unsafe_public_url_not_exported(url):
    assert history.publication_key('auto_ru', url) is None


def test_unregistered_cycle_excluded_from_history(tmp_path, monkeypatch):
    report_fixture(tmp_path, monkeypatch)
    result = history.load_history(tmp_path, cycle_ids=set())
    assert result['reports'] == [] and result['rows'] == []
