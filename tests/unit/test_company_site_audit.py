from app.service import company_site_audit

CATALOG = 'https://a1auto.ru/cars-for-sale/'
SITE_URL = 'https://a1auto.ru/cars-for-sale/v-vip_11_07.html'


def test_parse_catalogue_preserves_sale_status_and_price() -> None:
    html = '''
    <a href="/cars-for-sale/v-vip_11_07.html">В производстве V-VIP 46 990 000 ₽</a>
    <a href="/cars-for-sale/old.html">Продано V-Class 14 000 000 ₽</a>
    <a href="/cars-for-sale/">Каталог</a>
    '''

    rows = company_site_audit.parse_company_site_catalog(html, CATALOG)

    assert len(rows) == 2
    assert rows[0]['publication_state'] == 'sold'
    assert rows[1]['publication_state'] == 'published'
    assert rows[1]['price'] == 46_990_000


def test_active_marketing_row_without_vin_matches_company_site_by_url(monkeypatch) -> None:
    monkeypatch.setattr(company_site_audit.settings, 'company_site_catalog_url', CATALOG)

    class Reader:
        def __init__(self, _url):
            pass

        def read(self):
            from app.contracts import SourceRecord

            return [SourceRecord(
                row_number=42,
                source={
                    'brand_model': 'Mercedes-Benz VLE',
                    'source_status': 'Актуально',
                    'listing_url': SITE_URL,
                    'price_hint': '46 990 000',
                },
                raw={},
            )]

    report = company_site_audit.audit_company_site(
            fetcher=lambda _: {
                'requested_url': CATALOG,
                'final_url': CATALOG,
                'http_status': 200,
                'candidates': [
                    {
                        'site_key': 'a1_site:/cars-for-sale/v-vip_11_07.html',
                        'url': SITE_URL,
                        'title': 'В производстве V-VIP',
                        'price': 46_990_000,
                        'publication_state': 'published',
                    }
                ],
            },
            reader_factory=Reader,
        )

    assert report['summary']['issues_total'] == 0
    assert report['summary']['published_catalogue_candidates'] == 1
