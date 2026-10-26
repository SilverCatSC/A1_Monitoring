import pytest

from app.service.vehicle_content import vins_conflict


@pytest.mark.parametrize(
    'registry,card,conflict',
    [
        ('X89183511M1GB1114\nW1V44781313871282', 'W1V44781313871282', False),
        ('X89183511M1GB1114 / W1V44781313871282', 'w1v44781313871282', False),
        ('X89183511M1GB1114', 'W1V44781313871282', True),
        ('Не указан', 'W1V44781313871282', False),
        ('W1V44781313871282', None, False),
    ],
)
def test_compares_documented_vins_as_attributes(registry, card, conflict):
    assert vins_conflict(registry, card) is conflict


def test_real_structured_labels_ignore_description_and_keep_owner_range():
    from app.service.vehicle_content import structured_vehicle_fields

    html = ''.join(
        f'<li class="CardInfoSummarySimpleRow-CY5TE"><div>{k}</div><div>{v}</div></li>'
        for k, v in [('Год выпуска', '2021'), ('Пробег', '76\u00a0470 км'), ('Владельцы', '4 и более')]
    )
    html += '<div class="CardDescription"><li class="CardInfoRow"><div>Пробег</div><div>1 км</div></li></div>'
    fields = structured_vehicle_fields(html)
    assert fields['year_attribute'] == 2021 and fields['mileage_km'] == 76470
    assert fields['owners_count'] is None and fields['owners_minimum'] == 4


def test_zero_mileage_is_known_approximate_mileage_and_conflicting_values_are_unknown():
    from app.service.vehicle_content import structured_vehicle_fields

    def row(v):
        return f'<li class="CardInfoRow"><div>Пробег</div><div>{v}</div></li>'

    assert structured_vehicle_fields(row('0 км'))['mileage_km'] == 0
    assert structured_vehicle_fields(row('50 тыс. км'))['mileage_km'] is None
    fields = structured_vehicle_fields(row('50 000 км') + row('60 000 км'))
    assert fields['mileage_km'] is None and 'mileage_raw' in fields['attribute_ambiguities']
