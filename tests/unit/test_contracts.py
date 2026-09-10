from app.contracts import canonicalize_headers, map_row, normalize_header, split_brand_model


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
    assert mapped['brand_model'] == 'Mercedes-Benz V-Class'
    assert mapped['vin'] == 'W1VVNLTZ5S4556796'
    assert mapped['listing_url_auto_ru'].startswith('https://auto.ru/')
    assert mapped['listing_url_avito'].startswith('https://www.avito.ru/')


def test_a1auto_header_is_mapped_to_direct_listing_url():
    headers = canonicalize_headers(['a1auto.ru'])
    mapped = map_row(headers, {'a1auto.ru': 'https://a1auto.ru/cars-for-sale/v-vip_11_07.html'})

    assert mapped['listing_url'] == 'https://a1auto.ru/cars-for-sale/v-vip_11_07.html'


def test_combined_vehicle_name_is_split_only_for_known_brand_prefixes():
    assert split_brand_model('Mercedes-Benz V-Class') == ('Mercedes-Benz', 'V-Class')
    assert split_brand_model('Land Rover Range Rover') == ('Land Rover', 'Range Rover')
    assert split_brand_model('Zeekr 009') == ('Zeekr', '009')
    assert split_brand_model('Unknown Future Model X') == ('Unknown Future Model X', None)
