from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.build_ai_work_units import build_units
from scripts.run_ai_work_units import (
    _call_hermes,
    _compose_final,
    _extract_json,
    _normalize_findings,
)
from scripts.validate_ai_review import main as validate_review_main
from scripts.validate_staged_review import validate_staged


def test_work_units_group_data_and_images_by_vehicle(tmp_path) -> None:
    image = tmp_path / 'card.png'
    image.write_bytes(b'png')
    packet = {
        'contract': {'agents_cannot_change_monitoring_data': True},
        'scan': {'runs': [{'source': 'auto_ru', 'status': 'success', 'notes': 'large'}]},
        'head_table_audit': {
            'summary': {'issues': 1},
            'issues': [{'vehicle_key': 'vin:ONE', 'vin': 'ONE', 'code': 'price'}],
            'content_samples': [{
                'vehicle_key': 'vin:ONE',
                'vin': 'ONE',
                'source': 'auto_ru',
                'card_text': 'visible',
                'card_evidence': 'card.png',
            }],
            'direct_card_samples': [{
                'vehicle_key': 'vin:ONE',
                'vin': 'ONE',
                'source': 'avito',
                'listing_url': 'https://www.avito.ru/moskva/avtomobili/car_1234567890',
                'status_code': 'active',
                'card': {'price': 1_000_000, 'vat_status': 'С НДС'},
                'card_evidence': 'card.png',
            }],
        },
        'company_site_audit': {'summary': {}, 'issues': []},
    }

    global_context, units = build_units(packet, tmp_path)

    assert len(units) == 1
    assert units[0]['data']['vehicle_key'] == 'vin:ONE'
    assert units[0]['images'][0]['path'] == str(image.resolve())
    assert units[0]['data']['direct_card_samples'][0]['card']['vat_status'] == 'С НДС'
    assert 'notes' not in global_context['scan']['runs'][0]


def test_runner_extracts_json_from_quiet_cli_suffix() -> None:
    value = _extract_json('prefix\n{"verdict":"ok"}\nsession: local')

    assert value == {'verdict': 'ok'}


def test_runner_retries_only_a_failed_small_unit(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output = Path(command[command.index('--output') + 1])
        output.write_text('not-json' if len(calls) == 1 else '{"verdict":"ok"}', encoding='utf-8')
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr('scripts.run_ai_work_units.subprocess.run', fake_run)
    monkeypatch.setattr('scripts.run_ai_work_units.time.sleep', lambda _: None)

    result, error = _call_hermes(Path('/fake/hermes'), tmp_path, 'unit', 'prompt')

    assert result == {'verdict': 'ok'}
    assert error is None
    assert len(calls) == 2
    assert (tmp_path / 'unit.retry2.raw.txt').is_file()


def test_runner_drops_findings_without_saved_evidence() -> None:
    rows = _normalize_findings([
        {'severity': 'medium', 'source': 'auto_ru', 'field': 'price', 'evidence': '', 'recommendation': 'check'},
        {'severity': 'medium', 'source': 'auto_ru', 'field': 'price', 'evidence': 'visible', 'recommendation': 'check'},
    ], vehicle_key='vin:ONE')

    assert len(rows) == 1
    assert rows[0]['evidence'] == 'visible'


def test_review_validator_accepts_company_site_vehicle_key(tmp_path, monkeypatch) -> None:
    packet = tmp_path / 'packet.json'
    review = tmp_path / 'review.json'
    packet.write_text(
        '{"head_table_audit":{"issues":[],"content_samples":[]},'
        '"company_site_audit":{"issues":[{"vehicle_key":"model:vle"}]}}',
        encoding='utf-8',
    )
    review.write_text(
        '{"verdict":"review_required","summary":"Нужна проверка",'
        '"issues":[{"severity":"medium","source":"a1_site",'
        '"vehicle_key":"model:vle","vin":null,"evidence":"Карточка найдена",'
        '"recommendation":"Сверить данные"}]}',
        encoding='utf-8',
    )
    monkeypatch.setattr(
        'sys.argv',
        ['validate_ai_review.py', str(review), '--allowed-vehicles-from', str(packet)],
    )

    assert validate_review_main() == 0


def test_final_review_can_only_select_existing_findings() -> None:
    compact = {
        'coverage': {},
        'units': [{
            'vehicle_key': 'model:vle',
            'vin': None,
            'findings': [{
                'severity': 'high',
                'source': 'auto_ru',
                'evidence': 'Сохранённый снимок показывает ошибку страницы.',
                'recommendation': 'Повторить проверку вручную.',
            }],
        }],
    }
    ranking = {
        'selected': [
            {'vehicle_key': 'invented:key', 'finding_index': 0},
            {'vehicle_key': 'model:vle', 'finding_index': 0},
        ],
    }

    review = _compose_final(compact, ranking, [])

    assert review['verdict'] == 'review_required'
    assert len(review['issues']) == 1
    assert review['issues'][0]['vehicle_key'] == 'model:vle'


def test_staged_validator_rejects_issue_without_finding_provenance() -> None:
    data = {
        'mode': 'sequential_multimodal',
        'coverage': {
            'units_completed': 1,
            'vision_expected': 0,
            'technical_failures': 0,
        },
        'failures': [],
        'unit_reports': [{
            'vehicle_key': 'model:vle',
            'data': {'findings': []},
            'vision': [],
        }],
        'hermes_review': {
            'verdict': 'review_required',
            'summary': 'Нужно проверить карточку.',
            'issues': [{
                'severity': 'high',
                'source': 'auto_ru',
                'vehicle_key': 'model:vle',
                'vin': None,
                'evidence': 'Придуманный факт.',
                'recommendation': 'Проверить.',
            }],
        },
    }

    try:
        validate_staged(data)
    except ValueError as exc:
        assert 'provenance' in str(exc)
    else:
        raise AssertionError('unsupported issue must be rejected')
