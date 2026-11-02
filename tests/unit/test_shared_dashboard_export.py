import pytest
from bs4 import BeautifulSoup

from scripts.export_shared_dashboard import project_html, public_url


def fixture_html():
    return '''<html><body><aside>PRIVATE NAV</aside>
    <a href="/api/v1/status/cycles/230c09ee-b8b6-4c65-93e1-3fb557964c7e/placement-report">JSON</a>
    <section id="business-review"><p>Incomplete: unknown is not inactive</p>
    <a href="#monitoring-evidence">Proof</a></section>
    <div class="monitor-tabs"></div><section id="vehicle-panel">
    <article class="vehicle-tile" data-search="example"><p>Price 19990000</p>
    <img src="https://a1auto.ru/upload/car.jpg" onerror="bad()">
    <a href="https://auto.ru/cars/used/sale/example/123/">Advert</a>
    <a href="https://docs.google.com/spreadsheets/private">Internal workbook</a>
    <a href="/api/v1/status/private-evidence">Evidence</a>
    <details><summary>Ячейки источника</summary>PRIVATE CELL</details>
    <script>alert('bad')</script><form action="/mutation">Edit</form></article></section>
    <section id="marketing-panel">Marketing issues</section><section id="publication-alerts">Signals</section>
    <details id="monitoring-evidence">PRIVATE RAW DATA</details></body></html>'''


def test_projection_retains_findings_and_excludes_internal_data():
    html, cycle = project_html(fixture_html())
    soup = BeautifulSoup(html, 'html.parser')
    assert cycle == '230c09ee-b8b6-4c65-93e1-3fb557964c7e'
    assert len(soup.select('.vehicle-tile')) == 1
    assert 'Price 19990000' in html
    assert 'unknown is not inactive' in html
    assert 'Marketing issues' in html
    assert soup.select_one('a[href="https://auto.ru/cars/used/sale/example/123/"]')
    assert not soup.select('form, aside, [onerror], #monitoring-evidence')
    assert [tag.get('src') for tag in soup.select('script')] == ['static/monitoring.js']
    for forbidden in ('/api/', 'docs.google.com', 'PRIVATE', 'bad()', '/mutation'):
        assert forbidden not in html


@pytest.mark.parametrize('url', ['http://auto.ru/a', 'https://auto.ru.evil/a',
                               'https://user:pass@auto.ru/a', '//auto.ru/a',
                               'https://auto.ru:888/a', 'javascript:alert(1)'])
def test_rejects_unsafe_links(url):
    assert not public_url(url)


def test_images_require_approved_media_origin():
    assert public_url('https://storage.yandexcloud.net/a1autofeed/id/1.jpg', image=True)
    assert not public_url('https://storage.yandexcloud.net/other/id/1.jpg', image=True)
    assert not public_url('https://auto.ru/private', image=True)


def test_missing_report_fails_closed():
    with pytest.raises(ValueError):
        project_html('<html>Report unavailable</html>')
