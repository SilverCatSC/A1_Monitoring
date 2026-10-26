"""Only the exact brand-catalogue s=profile_search_show_all may disappear after the manual gate."""
from app.scraper.challenge_retry import _same_requested_page

BRAND = 'https://www.avito.ru/brands/a1auto/all/avtomobili'


def test_approved_brand_catalogue_may_lose_the_show_all_parameter():
    assert _same_requested_page(
        BRAND + '?s=profile_search_show_all&sellerId=e4036231f69cc5cfc365db2f79600230',
        BRAND,
    )
    # The items/ path variant of the same brand catalogue is the same page.
    assert _same_requested_page(
        'https://www.avito.ru/brands/a1auto/items/all/avtomobili?s=profile_search_show_all',
        BRAND,
    )


def test_search_sorting_parameter_must_survive():
    assert not _same_requested_page(
        'https://www.avito.ru/moskva/avtomobili?s=104',
        'https://www.avito.ru/moskva/avtomobili',
    )
    assert not _same_requested_page(
        'https://www.avito.ru/moskva/avtomobili?s=104&p=2',
        'https://www.avito.ru/moskva/avtomobili?p=2',
    )


def test_brand_catalogue_keeps_every_other_s_value():
    assert not _same_requested_page(
        BRAND + '?s=104',
        BRAND,
    )
    assert not _same_requested_page(
        BRAND + '?s=profile_search',
        BRAND,
    )


def test_brand_catalogue_keeps_pagination_and_seller_identity():
    assert not _same_requested_page(
        BRAND + '?s=profile_search_show_all&p=2',
        BRAND,
    )
    assert not _same_requested_page(
        BRAND + '?s=profile_search_show_all',
        'https://www.avito.ru/brands/other_seller/all/avtomobili',
    )


def test_non_brand_pages_never_lose_the_s_parameter():
    assert not _same_requested_page(
        'https://www.avito.ru/moskva/avtomobili/mercedes_9876543210?s=profile_search_show_all',
        'https://www.avito.ru/moskva/avtomobili/mercedes_9876543210',
    )
