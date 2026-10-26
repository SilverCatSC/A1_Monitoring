import pytest

from app.models import EngineType
from app.scraper.seller import _card_price, _direct_card_fields


@pytest.mark.parametrize('text,expected', [
    ('Выше оценки на 23% 5 600 000 ₽', 5600000),
    ('Ниже оценки на 5% 10\u00a0900\u00a0000 ₽', 10900000),
    ('5\u202f600\u202f000 руб.', 5600000),
    ('5600000', 5600000),
    ('5600000.00', 5600000),
    ('Цена по запросу', None),
    ('10 000 000 ₽ 9 000 000 ₽', None),
    ('23% 5600000', None),
    ('', None),
])
def test_money_token_is_not_concatenated_with_other_numbers(text, expected):
    assert _card_price(text) == expected


def test_real_auto_ru_price_assessment_does_not_inflate_card_price():
    html = ('<h1>Mercedes-Benz V-Класс, 2020</h1>'
            '<div class="PriceUsedOffer"><span>Выше оценки на 23%</span>'
            '<span>5 600 000 ₽</span></div>'
            '<div class="CardOfferDescription">Описание автомобиля</div>')
    card = _direct_card_fields(html, EngineType.AUTO_RU)
    assert card['price'] == 5600000
    assert card['year'] == 2020
