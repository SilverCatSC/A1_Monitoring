from datetime import date

import pytest

from app.service.placement_identity import (
    PlacementIdError,
    format_placement_id,
    parse_placement_id,
    placement_id_from_description,
)


@pytest.mark.parametrize(
    ('value', 'brand_model', 'type_code', 'vehicle_year', 'placed_on', 'ordinal'),
    [
        ('MBVC011120260101260001', 'MBVC', '0111', 2026, date(2026, 1, 1), 1),
        ('HOH6022120241506260206', 'HOH6', '0221', 2024, date(2026, 6, 15), 206),
        ('RRSV022320222404260178', 'RRSV', '0223', 2022, date(2026, 4, 24), 178),
    ],
)
def test_owner_examples_are_exact_22_character_placement_ids(
    value, brand_model, type_code, vehicle_year, placed_on, ordinal
):
    parsed = parse_placement_id(value)

    assert len(parsed.value) == 22
    assert parsed.brand_model_code == brand_model
    assert parsed.vehicle_type_code == type_code
    assert parsed.vehicle_year == vehicle_year
    assert parsed.placement_date == placed_on
    assert parsed.ordinal == ordinal
    assert format_placement_id(
        brand_model_code=brand_model,
        vehicle_type_code=type_code,
        vehicle_year=vehicle_year,
        placement_date=placed_on,
        ordinal=ordinal,
    ) == value


def test_vehicle_year_and_placement_year_are_independent():
    parsed = parse_placement_id('HOH6022120241506260206')

    assert parsed.vehicle_year == 2024
    assert parsed.placement_date.year == 2026


@pytest.mark.parametrize(
    'value',
    [
        'MBVC011120260101260000',  # ordinal must start from 1
        'MBVC111120260101260001',  # first type-code character must be 0
        'MBVC011120260332260001',  # invalid calendar date
        'МБВК011120260101260001',  # no ambiguous Cyrillic token
        'MBVC01112026010126001',   # not 22 characters
    ],
)
def test_invalid_placement_ids_fail_closed(value):
    with pytest.raises(PlacementIdError):
        parse_placement_id(value)


@pytest.mark.parametrize(
    ('description', 'expected'),
    [
        ('A1 ID: MBVC011220262508260027\nMercedes-Benz V-Class', 'MBVC011220262508260027'),
        ('Идентификатор № HOH6022120241506260206', 'HOH6022120241506260206'),
        ('Mercedes-Benz V-Class MBVC011220262508260027', None),
        ('ID: MBVC011220262508260000', None),
    ],
)
def test_placement_id_in_description_requires_a_label_and_valid_contract(description, expected):
    assert placement_id_from_description(description) == expected
