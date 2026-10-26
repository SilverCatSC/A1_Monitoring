"""Transport status and content evidence stay separate facts on a direct card."""
import pytest

from app.models import EngineType
from app.scraper.seller import direct_page_status

CARD_URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_2ee0cf7eaeac'

# Structure follows the 2026-10-07 evidence: HTTP 439 transport error while the
# rendered document is a fully working card with the same ID, title, price and
# seller description.
ACTIVE_CARD_HTML = '''
<h1>Mercedes-Benz V-класс 2.1 AT, 2019</h1>
<div data-marker="item-view/item-price">9 900 000 ₽</div>
<div data-marker="item-view/item-description">
  ID: MBVC011220262508260027. Автомобиль в наличии. VIN W1VVNLTZXT4617043.
</div>
'''

REMOVED_CARD_HTML = '<h1>Mercedes-Benz V-класс</h1><div>Объявление снято с публикации.</div>'


def test_http_439_with_rendered_active_card_keeps_fields_and_uncertainty():
    result = direct_page_status(ACTIVE_CARD_HTML, EngineType.AVITO, CARD_URL, CARD_URL, 439)

    assert result['state'] == 'unknown'
    assert result['status_code'] == 'http_error'
    assert result['http_status'] == 439
    assert result['content_state'] == 'active'
    assert 'это не доказывает продажу' in result['reason']
    assert '2xx' in result['reason']
    # Разобранные поля сохранены и не превращают результат в успех.
    assert result['card']['title'].startswith('Mercedes-Benz V-класс')
    assert result['card']['price'] == 9_900_000
    assert result['card']['placement_id'] == 'MBVC011220262508260027'
    assert result['card']['vin'] == 'W1VVNLTZXT4617043'


def test_http_439_error_shell_reports_unrecognised_content():
    result = direct_page_status('<h1>Не найдено</h1>', EngineType.AVITO, CARD_URL, CARD_URL, 439)

    assert result['state'] == 'unknown'
    assert result['status_code'] == 'http_error'
    assert result['http_status'] == 439
    # Заголовок-заглушка без цены и описания не считается действующей карточкой.
    assert result['content_state'] == 'unknown'


def test_http_439_with_removal_banner_keeps_unknown_and_notes_content():
    result = direct_page_status(REMOVED_CARD_HTML, EngineType.AVITO, CARD_URL, CARD_URL, 439)

    assert result['state'] == 'unknown'
    assert result['status_code'] == 'http_error'
    assert result['content_state'] == 'removed'
    assert result['card']['title'] == 'Mercedes-Benz V-класс'


@pytest.mark.parametrize('status', [403, 429])
def test_challenged_statuses_stay_blocked_before_any_content_parsing(status):
    result = direct_page_status(ACTIVE_CARD_HTML, EngineType.AVITO, CARD_URL, CARD_URL, status)

    assert result['state'] == 'blocked'
    assert result['status_code'] == 'blocked'
    assert 'card' not in result
    assert 'content_state' not in result


def test_blocked_markup_under_439_stays_blocked():
    result = direct_page_status('<h1>Доступ ограничен</h1>', EngineType.AVITO, CARD_URL, CARD_URL, 439)

    assert result['state'] == 'blocked'
    assert 'card' not in result


def test_redirect_with_http_439_stays_redirect():
    other = CARD_URL.replace('2ee0cf7eaeac', 'c98fdb0cec64')
    result = direct_page_status(ACTIVE_CARD_HTML, EngineType.AVITO, CARD_URL, other, 439)

    assert result['state'] == 'unknown'
    assert result['status_code'] == 'redirected'
    assert 'card' not in result


def test_login_destination_stays_blocked():
    result = direct_page_status(ACTIVE_CARD_HTML, EngineType.AVITO, CARD_URL,
                                'https://auth.avito.ru/login', 200)

    assert result['state'] == 'blocked'
    assert 'card' not in result


def test_clean_transport_active_card_is_unchanged():
    result = direct_page_status(ACTIVE_CARD_HTML, EngineType.AVITO, CARD_URL, CARD_URL, 200)

    assert result['state'] == 'active'
    assert result['status_code'] == 'active'
    assert 'http_status' not in result
    assert 'content_state' not in result


def test_clean_transport_removal_banner_is_unchanged():
    result = direct_page_status(REMOVED_CARD_HTML, EngineType.AVITO, CARD_URL, CARD_URL, 200)

    assert result['state'] == 'removed'
    assert result['status_code'] == 'unpublished'


def test_recommendation_block_after_active_card_stays_active():
    html = ACTIVE_CARD_HTML + '<h2>Похожие объявления</h2><div>Mercedes-Benz V-класс</div>'
    result = direct_page_status(html, EngineType.AVITO, CARD_URL, CARD_URL, 200)

    assert result['state'] == 'active'
