from pathlib import Path

import pytest

from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter
from app.scraper.base import classify_result_page

FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'marketplaces'


@pytest.mark.parametrize(
    ('source', 'fixture_name', 'expected_state'),
    [
        ('auto_ru', 'auto_ru_captcha.html', 'blocked'),
        ('avito', 'avito_blocked.html', 'blocked'),
        ('avito', 'avito_empty.html', 'empty'),
    ],
)
def test_known_marketplace_challenges_and_empty_pages_have_fail_closed_contracts(
    source, fixture_name, expected_state
):
    html = (FIXTURES / fixture_name).read_text(encoding='utf-8')
    adapter = AutoRuAdapter() if source == 'auto_ru' else AvitoAdapter()

    assert adapter._extract(html, 1) == []
    assert classify_result_page(html, 0)[0] == expected_state


def test_unknown_auto_ru_layout_is_not_treated_as_an_empty_result():
    html = (FIXTURES / 'auto_ru_layout_unknown.html').read_text(encoding='utf-8')

    assert AutoRuAdapter()._extract(html, 1) == []
    assert classify_result_page(html, 0) == (
        'unrecognized',
        'no listing cards and no explicit empty-result marker',
    )
