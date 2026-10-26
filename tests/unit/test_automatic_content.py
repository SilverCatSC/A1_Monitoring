import hashlib

from app.models import EngineType
from app.scraper.seller import _direct_card_fields
from app.service.automatic_content import automatic_content_review


def review(description, **fields):
    card = {
        'title': 'Mercedes-Benz V-Класс, 2026',
        'description': description,
        'description_capture': {
            'software_truncated': False,
            'sha256': hashlib.sha256(description.encode()).hexdigest(),
        },
        **fields,
    }
    r = automatic_content_review(
        {
            'cycle_id': 'cycle',
            'observed_cards': [
                {
                    'source': 'auto_ru',
                    'url': 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334801-a/',
                    'state': 'active',
                    'card': card,
                }
            ],
        }
    )
    return r, {f['code'] for f in r['cards'][0]['findings']}


def test_cross_field_conflicts_are_reported_without_workbook_or_vin_identity():
    r, codes = review(
        'Год выпуска: 2025\nПробег: 90 000 км\nЦена: 10 000 000 ₽\nБез НДС',
        year_attribute=2026,
        mileage_km=73000,
        price=11000000,
        vat_attribute='С НДС',
    )
    assert {
        'year_description_conflict',
        'mileage_description_conflict',
        'price_description_conflict',
        'vat_description_conflict',
    } <= codes
    assert len(r['cards'][0]['fields']) == 6 and r['cards'][0]['unique_id'] is None
    assert r['source_writes'] is False and 'отдельный этап' in r['scope']


def test_vat_options_factory_equipment_and_custom_interior_are_not_conflicts():
    r, codes = review(
        'Год выпуска: 2026\nКомплектация: Business Jet\nВозможна оплата с НДС и без НДС.\nЦена с НДС: 12 000 000 ₽\nЦена без НДС: 10 000 000 ₽',
        price=12000000,
        factory_equipment='AMG Line',
        vat_attribute='С НДС',
    )
    assert 'vat_options' in codes and 'vat_description_conflict' not in codes
    assert 'price_description_conflict' not in codes
    assert not any('equipment_conflict' == c for c in codes)


def test_year_is_not_inferred_from_id_or_generation_ranges():
    _, codes = review('ID: MBVC011220252508260001\nПоколение 2023–2026. Выпускаем 250 ежегодно.')
    assert 'year_description_conflict' not in codes


def test_structured_and_title_years_remain_separate():
    html = '<h1>Mercedes-Benz, 2026</h1><li class="CardInfoRow"><div>Год выпуска</div><div>2025</div></li>'
    card = _direct_card_fields(html, EngineType.AUTO_RU)
    assert card['year_title'] == 2026 and card['year_attribute'] == 2025
    _, codes = review(
        'Описание', **{k: v for k, v in card.items() if k not in {'description', 'description_capture'}}
    )
    assert 'year_title_attribute_conflict' in codes


def test_missing_unknown_fields_are_not_fabricated_and_zero_is_compared():
    r, codes = review('Год выпуска: 2026\nПробег: 10 км', mileage_km=0)
    assert 'mileage_description_conflict' in codes
    assert r['cards'][0]['owners_count'] is None
    _, codes = review('Описание', mileage_km=None)
    assert 'mileage_description_conflict' not in codes


def test_truncated_or_missing_capture_is_not_treated_as_complete():
    _, codes = review('Описание', description_capture={'software_truncated': True})
    assert 'description_capture_unverified' in codes


def test_cyrillic_aliases_are_used_for_text_analysis_not_id_binding():
    r, codes = review('ID: МBVC011220252508260001\nГoд выпуcка: 2025')
    assert 'year_description_conflict' in codes
    assert r['cards'][0]['unique_id'] is None


def test_dashboard_can_render_automatic_checks_without_manual_review():
    from pathlib import Path

    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    from app.service.monitoring_digest import monitoring_digest
    from app.service.vehicle_dashboard import vehicle_dashboard

    content, _ = review('Год выпуска: 2025')
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({'base.html': '{% block content %}{% endblock %}'}),
                FileSystemLoader(str(Path(__file__).resolve().parents[2] / 'src/app/templates')),
            ]
        ),
        autoescape=True,
    )
    r = {
        'created_at_utc': '2026-10-20',
        'catalogue_complete': {},
        'new_card_checks': 0,
        'findings': [],
        'observed_cards': [],
        'automatic_content_checks': content,
    }
    output = env.get_template('placement_identity.html').render(
        data={
            'cycle': {'id': 'cycle'}, 'report': r, 'review': [], 'available_cycles': [],
            'monitoring': monitoring_digest(r), 'vehicles': vehicle_dashboard(r),
        }
    )
    assert 'Год в описании расходится с карточкой' in output
    assert 'Сверка с маркетинговой таблицей' in output
