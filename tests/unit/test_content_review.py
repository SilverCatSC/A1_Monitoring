import hashlib
import json

import pytest

from app.service.content_review import content_claims, read_content_review


def test_mixed_alphabets_do_not_hide_year_vat_or_custom_package():
    claims = content_claims('ID: MBVC011220252508260007 Гoд выпуcка: 2025 '
                            'Вoзможность оплаты: бeз HДC Комплектация: V-VIР Пробег: без пробега')
    assert claims['description_years'] == [2025]
    assert claims['vat'] == 'without'
    assert 'V-VIP' in claims['custom_packages']
    assert claims['mileage_km'] == [] and claims['without_mileage']


def test_identity_year_generation_and_company_volume_are_not_vehicle_year_or_mileage():
    claims = content_claims('ID: MBVC011220252508260001 Поколение 2023–2026. '
                            'Более 30 лет, выпускаем 250 ежегодно. Пробег: 73 000 км.')
    assert claims['description_years'] == []
    assert claims['mileage_km'] == [73000]


def test_factory_and_custom_equipment_and_alternative_vat_are_distinct():
    claims = content_claims('КОМПЛЕКТАЦИЯ BUSINESS JET. Комплектация: Exclusive. '
                            'Возможность оплаты: без НДС. Возможен расчёт с НДС.')
    assert claims['custom_packages'] == ['Business Jet']
    assert claims['vat'] == 'options'
    assert not claims['powertrain_in_equipment_label']
    assert content_claims('Комплектация: Последовательный гибрид (PHEV)')['powertrain_in_equipment_label']


def test_complete_boilerplate_and_specific_description_are_distinct():
    assert content_claims('ID: MBSP011220242209260035 Статус наличия: На заказ '
                          '——— A1 АВТО — часть группы компаний')['generic_description']
    assert not content_claims('Год выпуска: 2024 Пробег: 10 км. '
                              '——— A1 АВТО — часть группы компаний')['generic_description']


@pytest.mark.parametrize('case', ['valid', 'wrong_cycle', 'tamper', 'symlink', 'unsafe_url'])
def test_only_cycle_bound_local_content_review_is_loaded(tmp_path, case):
    relative = 'cycles/cycle-1/content_review/review.json'
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    review = {'schema_version': 1, 'cycle_id': 'cycle-1', 'new_live_scan': False, 'source_writes': False,
              'summary': {}, 'cards': [{'source': 'avito',
                'url': 'https://www.avito.ru/moskva/avtomobili/voyah_dream_8302635260',
                'findings': [], 'fields': [{'label': 'Цена', 'observed': '15 500 000',
                                         'reference': 'L29', 'verdict': 'Совпадает'}] * 6}]}
    if case == 'wrong_cycle':
        review['cycle_id'] = 'cycle-2'
    if case == 'unsafe_url':
        review['cards'][0]['url'] = 'javascript:alert(1)'
    path.write_text(json.dumps(review))
    report = {'cycle_id': 'cycle-1', 'business_review': {'content_review': {
        'report_path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}}
    if case == 'tamper':
        path.write_text(path.read_text() + ' ')
    if case == 'symlink':
        outside = tmp_path / 'outside.json'
        path.rename(outside)
        path.symlink_to(outside)
    assert bool(read_content_review(report, tmp_path)) == (case == 'valid')
