"""The declared offer count must come from the primary result scope or fail closed."""
from app.scraper.auto_ru import _all_offers_are_visible, _declared_offer_count


def test_ambiguous_rootless_show_more_buttons_fail_closed():
    # A recommendations strip before the filter's own button: the count is
    # ambiguous, so no exhausted claim may rely on either number.
    html = (
        '<div><button>Показать 9 предложений</button></div>'
        '<button>Показать 26 предложений</button>'
    )
    assert _declared_offer_count(html) is None
    assert not _all_offers_are_visible(_declared_offer_count(html), 9)


def test_primary_root_button_wins_over_an_earlier_outside_block():
    html = (
        '<div><button>Показать 9 предложений</button></div>'
        '<div class="ListingCars__items"><button>Показать 26 предложений</button></div>'
    )
    assert _declared_offer_count(html) == 26


def test_hidden_sidebar_button_is_ignored_before_any_counting():
    html = (
        '<aside hidden><button>Показать 9 предложений</button></aside>'
        '<button>Показать 26 предложений</button>'
    )
    assert _declared_offer_count(html) == 26


def test_css_hidden_removal_block_is_ignored():
    html = (
        '<div style="display: none"><button>Показать 9 предложений</button></div>'
        '<div style="visibility:hidden"><button>Показать 14 предложений</button></div>'
        '<button>Показать 26 предложений</button>'
    )
    assert _declared_offer_count(html) == 26


def test_duplicated_identical_buttons_count_once():
    html = (
        '<button>Показать 26 предложений</button>'
        '<button>Показать 26 предложений</button>'
    )
    assert _declared_offer_count(html) == 26


def test_disagreeing_primary_roots_fail_closed():
    html = (
        '<div class="ListingCars__items"><button>Показать 9 предложений</button></div>'
        '<div class="CardGroupOffersList__items"><button>Показать 26 предложений</button></div>'
    )
    assert _declared_offer_count(html) is None


def test_wrong_smaller_count_cannot_prove_exhaustion_at_parsed_cards():
    # Nine visible cards plus a stray "Показать 9" control must not equal a
    # proven complete catalogue when the real filter count is 26.
    ambiguous = (
        '<div><button>Показать 9 предложений</button></div>'
        '<button>Показать 26 предложений</button>'
    )
    declared = _declared_offer_count(ambiguous)
    assert declared is None
    assert not _all_offers_are_visible(declared, 9)


def test_active_radius_counter_stays_authoritative():
    html = (
        '<div class="ListingGeoRadiusCounters__item ListingGeoRadiusCounters__item_active">'
        '<span class="ListingGeoRadiusCounters__itemCount">14 предложений</span></div>'
        '<button>Показать 26 предложений</button>'
    )
    assert _declared_offer_count(html) == 14


def test_group_result_heading_wins_over_similar_model_button():
    html = ('<h2>Похожие автомобили</h2><button>Показать 1 предложение</button>'
            '<button>Показать 20 предложений</button>'
            '<h2>20 предложений от 36 300 000 до 48 000 000 ₽</h2>')
    assert _declared_offer_count(html) == 20


def test_single_group_offer_heading_and_other_city_results():
    html = ('<button>Показать 105 предложений</button><h2>Одно предложение</h2>'
            '<h2>Ещё 4 в других городах</h2><h3>4 предложения</h3>')
    assert _declared_offer_count(html) == 1


def test_short_group_count_does_not_make_missing_cards_complete():
    html = '<h2>8 предложений от 32 490 000 до 46 665 000 ₽</h2>'
    assert _declared_offer_count(html) == 8
    assert not _all_offers_are_visible(_declared_offer_count(html), 7)


def test_hidden_group_heading_cannot_prove_end():
    assert _declared_offer_count('<h2 hidden>Одно предложение</h2>') is None
