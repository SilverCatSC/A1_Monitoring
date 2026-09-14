from app.scraper.auto_ru import AutoRuAdapter, _all_offers_are_visible, _declared_offer_count, _list_url
from app.scraper.auto_ru import _page_url as auto_page_url
from app.scraper.avito import AvitoAdapter
from app.scraper.avito import _page_url as avito_page_url


def test_auto_ru_extracts_current_universal_listing_markup():
    html = """
    <div class="ListingItemUniversal-AbCdE">
      <div class="ListingItemUniversal__body-X">
        <div class="ListingItemTitle">
          <a class="Link ListingItemTitle__link ListingItemUniversalSpecs__link-X"
             href="https://auto.ru/cars/used/sale/mercedes/v_klasse/1132311022-old/?from=search">
            Mercedes-Benz V-Класс 2024
          </a>
        </div>
        <span>15 900 000 ₽</span>
      </div>
    </div>
    """
    hits = AutoRuAdapter()._extract(html, page_number=2)

    assert len(hits) == 1
    assert hits[0].external_id.endswith('?from=search')
    assert hits[0].title == 'Mercedes-Benz V-Класс 2024'
    assert hits[0].page_number == 2
    assert hits[0].position == 1
    assert hits[0].price == 15_900_000


def test_auto_ru_deduplicates_multiple_links_for_same_listing():
    html = """
    <div class="ListingItemUniversal-AbCdE">
      <a href="https://auto.ru/cars/used/sale/bmw/x5/1134019078-a/">BMW X5</a>
      <a href="https://auto.ru/cars/used/sale/bmw/x5/1134019078-b/?gallery=1">Фото</a>
    </div>
    """
    assert len(AutoRuAdapter()._extract(html, page_number=1)) == 1


def test_auto_ru_stops_after_short_catalogue_is_fully_parsed():
    assert _declared_offer_count('<h1>Maextro S800 — 2 предложения</h1>') == 2
    assert _all_offers_are_visible(2, 2)
    assert not _all_offers_are_visible(15, 14)
    assert not _all_offers_are_visible(None, 2)


def test_auto_ru_requests_list_view_for_model_catalogues():
    assert _list_url('https://auto.ru/moskva/cars/hongqi/hq9/new/?geo_radius=0&rid=213') == (
        'https://auto.ru/moskva/cars/hongqi/hq9/new/?geo_radius=0&rid=213&output_type=list'
    )
    assert _list_url('https://auto.ru/moskva/cars/hongqi/hq9/new/?output_type=grid&rid=213') == (
        'https://auto.ru/moskva/cars/hongqi/hq9/new/?output_type=list&rid=213'
    )


def test_auto_ru_excludes_sidebar_premium_and_uses_title_link():
    html = """
    <aside class="ListingPremiumItem">
      <a href="https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/1111111111-ad/">
        Боковая реклама
      </a>
    </aside>
    <div class="ListingItemUniversal-AbCdE">
      <a href="https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/2222222222-car/">
        Еще 12 фото
      </a>
      <a class="ListingItemTitle__link"
         href="https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/2222222222-car/">
        Mercedes-Benz V-Класс 2026
      </a>
    </div>
    """
    hits = AutoRuAdapter()._extract(html, page_number=1)
    assert len(hits) == 1
    assert hits[0].title == 'Mercedes-Benz V-Класс 2026'
    assert hits[0].position == 1


def test_auto_ru_extracts_lcv_dealer_listing_paths():
    html = """
    <div class="ListingItemUniversal-AbCdE">
      <a href="https://auto.ru/lcv/used/sale/mercedes/sprinter/1132284604-9b053bf0/">
        Mercedes-Benz Sprinter
      </a>
      <span>18 500 000 ₽</span>
    </div>
    """
    hits = AutoRuAdapter()._extract(html, page_number=1)

    assert len(hits) == 1
    assert hits[0].url.endswith('/1132284604-9b053bf0/')
    assert hits[0].price == 18_500_000


def test_auto_ru_extracts_current_new_group_offer_paths():
    html = """
    <a href="https://auto.ru/moskva/cars/new/group/maextro/s800/24059154-24059158/">
      Сводная карточка модели
    </a>
    <div class="ListingItemUniversal-AbCdE">
      <a class="Link ListingItemTitle__link ListingItemUniversalSpecs__link-X"
         href="https://auto.ru/cars/new/group/maextro/s800/24059170/24087222/1133298803-85734ae5/">
        Maextro S800, 2026
      </a>
      <span>27 550 000 ₽</span>
    </div>
    """

    hits = AutoRuAdapter()._extract(html, page_number=1)

    assert len(hits) == 1
    assert hits[0].url.endswith('/1133298803-85734ae5/')
    assert hits[0].title == 'Maextro S800, 2026'
    assert hits[0].price == 27_550_000


def test_auto_ru_uses_offer_root_when_model_summary_precedes_it():
    html = """
    <div class="ListingCars__items ListingCars__items_modelCars">
      <div class="ListingItemGroup">Модельная сводка без карточки продажи</div>
    </div>
    <div class="CardGroupOffersList__items">
      <div class="ListingItemUniversal-AbCdE">
        <a class="ListingItemTitle__link"
           href="https://auto.ru/cars/new/group/hongqi/hq9/1/2/1133149207-2529c1ee/">
          Hongqi HQ9
        </a>
        <span>8 490 000 ₽</span>
      </div>
    </div>
    """

    hits = AutoRuAdapter()._extract(html, page_number=1)

    assert len(hits) == 1
    assert hits[0].title == 'Hongqi HQ9'
    assert hits[0].price == 8_490_000


def test_avito_extracts_known_article_markup():
    html = """
    <div data-marker="catalog-serp">
      <article data-marker="item" data-item-name="Mercedes-Benz V-Класс">
        <a href="/moskva/avtomobili/mercedes-benz_v-klass_9876543210">Открыть</a>
        <span>12 500 000 ₽</span>
      </article>
    </div>
    """
    hits = AvitoAdapter()._extract(html, page_number=3)
    assert len(hits) == 1
    assert hits[0].url.startswith('https://www.avito.ru/')
    assert hits[0].page_number == 3
    assert hits[0].price == 12_500_000


def test_avito_extracts_current_div_listing_markup():
    html = """
    <div class="iva-item-root-Kcj9I js-catalog-item-enum"
         data-marker="item_list_with_filters/item(2)" data-item-id="8176083067">
      <a data-marker="item-photo-sliderLink"
         href="/moskva/avtomobili/hongqi_hq9_2.0_at_2026_8176083067">Фото</a>
      <h2><a data-marker="item-title"
             href="/moskva/avtomobili/hongqi_hq9_2.0_at_2026_8176083067">
        Hongqi HQ9 2.0 AT, 2026
      </a></h2>
      <span>11 745 000 ₽</span>
    </div>
    """

    hits = AvitoAdapter()._extract(html, page_number=1)

    assert len(hits) == 1
    assert hits[0].external_id.endswith('8176083067')
    assert hits[0].title == 'Hongqi HQ9 2.0 AT, 2026'
    assert hits[0].position == 1
    assert hits[0].price == 11_745_000


def test_avito_extracts_plain_item_div_and_pagination_replaces_old_page():
    html = """
    <div data-marker="item">
      <a data-marker="item-title"
         href="/moskva/avtomobili/zeekr_9x_2026_8176083999">Zeekr 9X</a>
    </div>
    """
    assert len(AvitoAdapter()._extract(html, page_number=1)) == 1
    assert avito_page_url('https://www.avito.ru/moskva/avtomobili?p=2&s=104', 3) == (
        'https://www.avito.ru/moskva/avtomobili?s=104&p=3'
    )
    assert auto_page_url('https://auto.ru/moskva/cars/all/?page=2&rid=213', 3) == (
        'https://auto.ru/moskva/cars/all/?rid=213&page=3'
    )
