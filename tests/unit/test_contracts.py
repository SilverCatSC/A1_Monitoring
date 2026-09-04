from app.contracts import canonicalize_headers, map_row, normalize_header


def test_map_row_keeps_first_non_empty_alias():
    headers = canonicalize_headers(['Brand', 'Марка', 'Фильтр AUTO-RU', 'search_url_auto_ru', 'Year', 'Год'])

    row = {
        'Brand': 'Toyota',
        'Марка': None,
        'Фильтр AUTO-RU': 'https://auto.ru/cars/1',
        'search_url_auto_ru': 'https://auto.ru/cars/2',
        'Year': 2020,
        'Год': '2019',
    }

    mapped = map_row(headers, row)

    assert mapped['brand'] == 'Toyota'
    assert mapped['search_url_auto_ru'] == 'https://auto.ru/cars/1'
    assert mapped['year'] == 2020


def test_actual_contract_headers_map_listing_links_not_filters():
    headers = canonicalize_headers(['Статус', 'Марка, модель', ' VIN номер', 'AUTO-RU', 'AVITO-RU'])
    mapped = map_row(
        headers,
        {
            'Статус': 'Актуально',
            'Марка, модель': 'Mercedes-Benz V-Class',
            ' VIN номер': 'W1VVNLTZ5S4556796',
            'AUTO-RU': 'https://auto.ru/cars/used/sale/x/123-a/',
            'AVITO-RU': 'https://www.avito.ru/moskva/avtomobili/x_456',
        },
    )

    assert normalize_header('Марка, модель') == 'марка_модель'
    assert mapped['source_status'] == 'Актуально'
    assert mapped['brand'] == 'Mercedes-Benz V-Class'
    assert mapped['vin'] == 'W1VVNLTZ5S4556796'
    assert mapped['listing_url_auto_ru'].startswith('https://auto.ru/')
    assert mapped['listing_url_avito'].startswith('https://www.avito.ru/')
