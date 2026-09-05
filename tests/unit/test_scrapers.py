from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter


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
