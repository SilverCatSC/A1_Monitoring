import httpx
import pytest

from app.importer import sheet_csv

URL = 'https://docs.google.com/spreadsheets/d/test/export?format=csv'


@pytest.mark.parametrize('failure', ['timeout', '503', '429'])
def test_export_retries_transient_failure(monkeypatch, failure):
    calls, waits = [], []

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            if failure == 'timeout':
                raise httpx.ConnectTimeout('timed out')
            return httpx.Response(int(failure), request=httpx.Request('GET', url))
        return httpx.Response(200, text='brand,model\n', request=httpx.Request('GET', url))

    monkeypatch.setattr(sheet_csv.httpx, 'get', get)
    monkeypatch.setattr(sheet_csv.time, 'sleep', waits.append)
    assert sheet_csv._download_sheet(URL).status_code == 200
    assert calls == [URL, URL]
    assert waits == [3]


def test_export_stops_after_three_failures_without_stale_fallback(monkeypatch):
    calls, waits = [], []

    def get(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectTimeout('timed out')

    monkeypatch.setattr(sheet_csv.httpx, 'get', get)
    monkeypatch.setattr(sheet_csv.time, 'sleep', waits.append)
    with pytest.raises(RuntimeError, match='No stale registry was used'):
        sheet_csv.CsvOrXlsxReader(URL).read()
    assert len(calls) == 3
    assert waits == [3, 6]


def test_export_does_not_retry_permission_failure(monkeypatch):
    monkeypatch.setattr(sheet_csv.httpx, 'get', lambda url, **kw: httpx.Response(
        403, request=httpx.Request('GET', url)))
    monkeypatch.setattr(sheet_csv.time, 'sleep', lambda _: pytest.fail('must not retry 403'))
    with pytest.raises(httpx.HTTPStatusError):
        sheet_csv._download_sheet(URL)
