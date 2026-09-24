import httpx
import pytest

from app.service.placement_feed_snapshot import (
    PlacementFeedError,
    read_placement_feed_snapshot,
    workbook_id_from_source,
)


def test_reader_preserves_ids_and_skips_explanatory_rows():
    def respond(request):
        gid = request.url.params['gid']
        if gid == '779695926':
            body = 'Title,,\ncar,unique_id,action,vin\ncar,MBVC011220262508260027,show,VIN123\n'
        else:
            body = (
                'Id,AvitoId,VIN\nОбязательный,Необязательный,\nПодробнее,Подробнее,\n'
                'MBVC011220262508260009,8176281881,VIN123\n'
            )
        return httpx.Response(200, text=body, headers={'content-type': 'text/csv'})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        snapshot = read_placement_feed_snapshot(
            'https://docs.google.com/spreadsheets/d/abc-123/export?format=csv&gid=10',
            client=client,
        )

    assert snapshot.first_data_rows == {
        'autoru-feed-all': 3, 'avito-feed-new': 4, 'avito-feed-used': 4,
    }
    assert snapshot.rows_by_sheet['avito-feed-new'] == [
        {'Id': 'MBVC011220262508260009', 'AvitoId': '8176281881', 'VIN': 'VIN123'},
    ]


@pytest.mark.parametrize('source', [
    'http://docs.google.com/spreadsheets/d/a/export?format=csv',
    'https://evil.test/spreadsheets/d/a/export?format=csv',
    '/tmp/feed.csv',
])
def test_reader_rejects_non_google_source(source):
    with pytest.raises(PlacementFeedError):
        workbook_id_from_source(source)


def test_reader_rejects_html_instead_of_csv():
    with httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, text='<html>login</html>',
                                        headers={'content-type': 'text/html'})
    )) as client:
        with pytest.raises(PlacementFeedError, match='expected CSV'):
            read_placement_feed_snapshot(
                'https://docs.google.com/spreadsheets/d/abc/export?format=csv', client=client,
            )
