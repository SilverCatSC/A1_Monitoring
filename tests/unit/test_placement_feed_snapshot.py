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


@pytest.mark.parametrize('failure', ['timeout', '503'])
def test_workbook_export_retries_temporary_failure(monkeypatch, failure):
    from app.service import placement_feed_snapshot as module
    calls, waits = [], []

    def respond(request):
        calls.append(1)
        if len(calls) == 1:
            if failure == 'timeout':
                raise httpx.ConnectTimeout('timed out', request=request)
            return httpx.Response(503)
        return httpx.Response(200, text='fresh export')

    monkeypatch.setattr(module.time, 'sleep', waits.append)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert module._get_export(client, 'https://docs.google.com/test').text == 'fresh export'
    assert len(calls) == 2 and waits == [3]


def test_workbook_export_stops_after_three_timeouts(monkeypatch):
    from app.service import placement_feed_snapshot as module
    calls, waits = [], []

    def respond(request):
        calls.append(1)
        raise httpx.ConnectTimeout('timed out', request=request)

    monkeypatch.setattr(module.time, 'sleep', waits.append)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(PlacementFeedError, match='ConnectTimeout'):
            read_placement_feed_snapshot('https://docs.google.com/spreadsheets/d/abc/export', client=client)
    assert len(calls) == 3 and waits == [3, 6]


def test_host_run_recovers_main_marketing_sheet_without_discarding_feeds(monkeypatch):
    # Other modules' tests reload app.config. Patch the object actually used by
    # this unit, rather than whichever settings instance was re-exported last.
    from app.service.source_recovery import settings, source_with_recovery

    monkeypatch.setattr(settings, 'network_profile', 'local_routed_diagnostic')
    monkeypatch.setattr(settings, 'local_browser_host_admission', True)
    monkeypatch.setattr('app.service.placement_feed_snapshot.time.sleep', lambda _: None)
    calls, events = [], []

    def respond(request):
        gid = request.url.params['gid']
        calls.append(gid)
        if gid == '0':
            if calls.count('0') <= 3:
                raise httpx.ConnectTimeout('temporary marketing export outage', request=request)
            header = [''] * 24
            for column, value in {5:'Статус',9:'Комплектация А1',10:'VIN номер',11:'Стоимость',
                                  12:'Год выпуска',13:'Пробег',15:'НДС',22:'AUTO-RU',23:'AVITO-RU'}.items():
                header[column] = value
            body = ','.join(header) + '\n'
        elif gid == '779695926':
            body = 'Title,,\ncar,unique_id,action,vin\ncar,MBVC011220262508260027,show,VIN123\n'
        else:
            body = 'Id,AvitoId,VIN\nОбязательный,Необязательный,\nПодробнее,Подробнее,\n'
        return httpx.Response(200, text=body, headers={'content-type':'text/csv'})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        snapshot = source_with_recovery(lambda: read_placement_feed_snapshot(
            'https://docs.google.com/spreadsheets/d/abc/export', client=client),
            phase='placement_feeds', progress=events.append)
    assert snapshot.marketing_sha256 and snapshot.marketing_error is None
    assert calls.count('779695926') == 1 and calls.count('0') == 4
    assert [e['event'] for e in events] == ['source_recovery_waiting', 'source_recovery_resolved']
