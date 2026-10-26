import asyncio
from contextlib import asynccontextmanager

import pytest

from app.models import EngineType
from app.scraper import seller
from app.scraper.seller import direct_page_status, wait_for_direct_card_content

URL = 'https://www.avito.ru/moskva/avtomobili/car_7888035437'
CARD = ('<h1>Mercedes-Benz V-класс, 2020</h1>'
        '<span data-marker="item-view/item-price">5 600 000 ₽</span>'
        '<div data-marker="item-view/item-description">В наличии. ID: MBVC022220232508260020.</div>')


class Page:
    url = URL

    def __init__(self, documents):
        self.documents = iter(documents)
        self.last = ''

    async def content(self):
        self.last = next(self.documents, self.last)
        return self.last


@pytest.mark.parametrize('status', [200, 439])
def test_waits_through_shell_and_partial_description_without_reloading(status):
    initial = '<main id="app"></main>'
    partial = '<h1>Mercedes-Benz V-класс, 2020</h1>'
    page = Page([partial, CARD, CARD])
    html, readiness = asyncio.run(wait_for_direct_card_content(
        page, EngineType.AVITO, URL, status, initial, notice_after_seconds=1, poll_seconds=0.01,
    ))
    assert readiness['state'] == 'ready'
    assert readiness['stable']
    assert readiness['missing_fields'] == []
    result = direct_page_status(html, EngineType.AVITO, URL, URL, status)
    assert result['card']['price'] == 5600000
    assert result['card']['placement_id'] == 'MBVC022220232508260020'
    if status == 439:
        assert result['status_code'] == 'http_error'
        assert result['content_state'] == 'active'
        assert result['state'] == 'unknown'
        assert result['http_status'] == 439
    else:
        assert result['state'] == 'active'


def test_slow_description_is_not_skipped_after_wait_notice():
    initial = '<h1>Car 2020</h1><span itemprop="price">100</span>'
    page = Page([initial] * 4 + [CARD, CARD])
    events = []
    html, readiness = asyncio.run(wait_for_direct_card_content(
        page, EngineType.AVITO, URL, 200, initial,
        notice_after_seconds=0.01, poll_seconds=0.01, progress=events.append,
    ))
    assert html == CARD
    assert readiness['state'] == 'ready'
    assert readiness['wait_seconds'] > 0.01
    assert events[0]['event'] == 'direct_content_waiting'
    assert events[0]['missing_fields'] == ['description_excerpt']
    assert events[0]['no_timeout_skip'] is True


def test_loaded_description_without_unique_id_does_not_wait_for_invented_identity():
    card = CARD.replace('ID: MBVC022220232508260020.', 'Описание автомобиля без ID.')
    page = Page([card])
    _, readiness = asyncio.run(wait_for_direct_card_content(
        page, EngineType.AVITO, URL, 200, card, notice_after_seconds=1, poll_seconds=0.01,
    ))
    assert readiness['state'] == 'ready'
    assert readiness['wait_seconds'] < 0.1


@pytest.mark.parametrize('status,html', [
    (403, '<h1>Доступ ограничен</h1>'),
    (200, '<h1>Объявление снято</h1>'),
])
def test_blocked_or_removed_cards_do_not_wait_for_fields(status, html):
    page = Page([])
    _, readiness = asyncio.run(wait_for_direct_card_content(
        page, EngineType.AVITO, URL, status, html, notice_after_seconds=1,
    ))
    assert readiness['state'] == 'terminal'
    assert readiness['wait_seconds'] < 0.1


@pytest.mark.parametrize('status,loaded,expected', [
    (200, CARD, 'active'),
    (439, CARD, 'http_error'),
    (200, CARD.replace('В наличии. ID: MBVC022220232508260020.', 'Описание отсутствует.'), 'content_incomplete'),
])
def test_inspection_uses_ready_snapshot_and_keeps_incomplete_checks_partial(monkeypatch, status, loaded, expected):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'captcha_operator_wait_seconds'):
        monkeypatch.setattr(seller.settings, key, 0)
    monkeypatch.setattr(seller.settings, 'card_content_notice_after_seconds', 0.25)
    page = Page(['<main id="app"></main>', loaded, loaded])
    page.visits = 0

    async def goto(*args, **kwargs):
        page.visits += 1
        return type('Response', (), {'status': status})()

    page.goto = goto

    @asynccontextmanager
    async def manager(*args, **kwargs):
        yield page

    async def evidence(*args, **kwargs):
        return 'card.png'

    monkeypatch.setattr(seller, 'async_playwright', manager)
    monkeypatch.setattr(seller, 'browser_page', manager)
    monkeypatch.setattr(seller, 'capture_page_evidence', evidence)
    result = asyncio.run(seller.inspect_direct_link(EngineType.AVITO, URL))
    assert page.visits == 1
    assert result['status_code'] == expected
    assert result['card']['title']
    assert result['evidence'] == 'card.png'
    if expected == 'active':
        assert result['content_readiness']['state'] == 'ready'
        assert result['card']['placement_id'] == 'MBVC022220232508260020'
    else:
        assert result['state'] == 'unknown'


def test_navigation_timeout_keeps_same_card_and_document_status(monkeypatch):
    from playwright.async_api import TimeoutError as NavigationTimeout

    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'captcha_operator_wait_seconds'):
        monkeypatch.setattr(seller.settings, key, 0)
    page = Page([CARD, CARD])
    page.main_frame = object()
    handlers = {}
    page.on = lambda name, callback: handlers.update({name: callback})
    page.remove_listener = lambda name, callback: handlers.pop(name)
    page.visits = 0
    response = type('Response', (), {
        'status': 200,
        'request': type('Request', (), {
            'is_navigation_request': lambda self: True,
            'frame': page.main_frame,
        })(),
    })()

    async def goto(*args, **kwargs):
        page.visits += 1
        handlers['response'](response)
        raise NavigationTimeout('Navigation timeout')

    page.goto = goto

    @asynccontextmanager
    async def manager(*args, **kwargs):
        yield page

    async def evidence(*args, **kwargs):
        return 'card.png'

    monkeypatch.setattr(seller, 'async_playwright', manager)
    monkeypatch.setattr(seller, 'browser_page', manager)
    monkeypatch.setattr(seller, 'capture_page_evidence', evidence)
    events = []
    result = asyncio.run(seller.inspect_direct_link(EngineType.AVITO, URL, progress=events.append))
    assert page.visits == 1
    assert result['status_code'] == 'active'
    assert result['card']['placement_id'] == 'MBVC022220232508260020'
    assert result['content_readiness']['state'] == 'ready'
    assert any(event['event'] == 'direct_navigation_waiting' for event in events)
    assert not handlers


def test_previous_page_cannot_end_check_before_new_document_arrives():
    page = Page([CARD, CARD])
    statuses = iter([None, 200, 200])
    html, readiness = asyncio.run(wait_for_direct_card_content(
        page, EngineType.AVITO, URL, None, '<h1>Объявление снято</h1>',
        require_document=True, status_provider=lambda: next(statuses), poll_seconds=0.01,
    ))
    assert html == CARD
    assert readiness['state'] == 'ready'
