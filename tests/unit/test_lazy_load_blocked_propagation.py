"""A marketplace denial during lazy load must halt the whole source, not just one filter."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base, EngineType, Listing, ListingObservation, SearchFilter, VehicleFilterExpectation
from app.scraper.avito import AvitoAdapter
from app.scraper.base import ScanResult
from app.service.monitor import MonitorService


class FakeAdapter:
    def __init__(self, results):
        self.results = list(results)

    async def scan_filter(self, _url, _max_pages, target_keys=None):
        return self.results.pop(0)


def _result(error, diagnostics, complete=False):
    return ScanResult(
        filter_id='filter',
        page_count=1,
        hits=[],
        diagnostics=diagnostics,
        scanned_at=datetime.now(UTC),
        requested_pages=3,
        complete=complete,
        error=error,
    )


def test_scan_was_blocked_distinguishes_denials_from_other_errors():
    assert MonitorService._scan_was_blocked(_result(
        'page 1: LazyLoadBlocked: lazy load blocked: CAPTCHA unresolved',
        {'page_1_state': 'blocked', 'page_1_block_reason': 'lazy load blocked: CAPTCHA unresolved'},
    ))
    # The explicit contract prefix is recognised even without the diagnostics.
    assert MonitorService._scan_was_blocked(_result(
        'page 1: LazyLoadBlocked: lazy load blocked: доступ ограничен', {},
    ))
    assert MonitorService._scan_was_blocked(_result(
        'page 1: RuntimeError: HTTP 429', {'page_1_state': 'technical_error'},
    ))
    # Ordinary parser, scope, evidence and no-progress failures stay technical.
    assert not MonitorService._scan_was_blocked(_result(
        'parser uncertainty on page 2: no listing cards', {'page_2_state': 'unrecognized'},
    ))
    assert not MonitorService._scan_was_blocked(_result(
        'page 1: RuntimeError: lazy load changed result scope or page',
        {'page_1_state': 'technical_error'},
    ))
    assert not MonitorService._scan_was_blocked(_result(
        'page 1: RuntimeError: lazy load made no progress; traversal is not proven',
        {'page_1_state': 'technical_error'},
    ))
    assert not MonitorService._scan_was_blocked(_result(
        'listing-card evidence capture failed page 1: avito:1000000001',
        {'page_1_state': 'evidence_missing'},
    ))


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "blocked.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _seed_two_filters(session):
    listing = Listing(
        vehicle_signature='W1VVNLTZ5S4556796',
        vin='W1VVNLTZ5S4556796',
        source_auto_ru='https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-abcd/',
        is_active=True,
    )
    first = SearchFilter(
        id='filter-1', source=EngineType.AUTO_RU, external_key='k1', name='First',
        raw_url='https://auto.ru/moskva/cars/mercedes/v_class/used/', active=True,
    )
    second = SearchFilter(
        id='filter-2', source=EngineType.AUTO_RU, external_key='k2', name='Second',
        raw_url='https://auto.ru/moskva/cars/mercedes/v_class/new/', active=True,
    )
    session.add_all([listing, first, second])
    session.flush()
    session.add(VehicleFilterExpectation(filter_id=first.id, listing_id=listing.id))
    session.add(VehicleFilterExpectation(filter_id=second.id, listing_id=listing.id))
    session.commit()


def test_lazy_load_denial_halts_remaining_filters_of_the_source(tmp_path, monkeypatch):
    monkeypatch.setattr('app.service.monitor.settings.scan_enabled_engines', 'auto_ru')
    monkeypatch.setattr('app.service.monitor.settings.network_profile', 'local_no_vpn')
    session, engine = _session(tmp_path)
    try:
        _seed_two_filters(session)
        blocked = _result(
            'page 1: LazyLoadBlocked: lazy load blocked: CAPTCHA unresolved',
            {'page_1_state': 'blocked', 'page_1_block_reason': 'lazy load blocked: CAPTCHA unresolved'},
        )
        adapter = FakeAdapter([blocked])

        summary = MonitorService(session, auto_adapter=adapter).run_full_cycle()

        assert adapter.results == []  # the second filter was never scanned
        assert summary['technical_errors'] == 2
        halted = [
            item.raw_payload['scan_diagnostics'].get('error')
            for item in session.query(ListingObservation).all()
        ]
        assert 'source scan halted after marketplace challenge' in halted
    finally:
        session.close()
        engine.dispose()


def test_lazy_load_captcha_during_batch_propagates_as_blocked(tmp_path, monkeypatch):
    """A CAPTCHA raised while waiting for the batch click turns into a blocked source."""
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'catalogue_lazy_load_timeout_seconds', 1.5)

    real_sleep = asyncio.sleep

    async def skip_legacy_refresh(seconds):
        if seconds >= 3:
            return
        await real_sleep(seconds)

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', skip_legacy_refresh)

    gate = '<h1>Подтвердите, что вы не робот</h1>'
    catalogue = (
        '<span data-marker="page-title/count">2</span>'
        '<div id="primary" data-marker="catalog-serp">'
        '<div data-marker="item" data-item-id="1000000001">'
        '<a data-marker="item-title" href="https://www.avito.ru/moskva/avtomobili/car_1000000001">Машина</a>'
        '</div><button id="more" onclick="loadMore()">Показать ещё</button></div>'
        '<script>async function loadMore() {'
        "document.querySelector('#primary').innerHTML = await (await fetch('/more')).text();"
        '}</script>'
    )
    documents, xhrs = [], []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                if route.request.resource_type == 'document':
                    documents.append(route.request.url)
                    body = catalogue if len(documents) == 1 else gate
                    await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)
                else:
                    xhrs.append(route.request.url)
                    await route.fulfill(content_type='text/html; charset=utf-8', body=gate)

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter(
                    'https://www.avito.ru/moskva/avtomobili', max_pages=3,
                )
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert xhrs == ['https://www.avito.ru/more']
    assert len(documents) == 2  # the initial page plus the single legacy refresh
    assert not result.complete
    assert 'LazyLoadBlocked' in (result.error or '')
    assert result.diagnostics['page_1_state'] == 'blocked'
    assert result.diagnostics['page_1_block_reason'].startswith('lazy load blocked')
    assert result.diagnostics['page_1_captcha_outcome'] == 'unresolved'
    assert result.diagnostics['page_1_captcha_refreshes'] == 1
    assert MonitorService._scan_was_blocked(result)
