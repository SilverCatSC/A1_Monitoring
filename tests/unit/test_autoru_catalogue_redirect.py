"""A real all-to-used dealer redirect must not hang or imply full inventory."""
import asyncio

import pytest
from test_autoru_navigation_recovery import (
    EXECUTION_CONTEXT_DESTROYED,
    AutoRuScanHarness,
    FakeTab,
    catalogue,
    fail_reopen,
)

from app.config import settings
from app.scraper.auto_ru import _condition_redirected
from app.scraper.challenge_retry import _same_recovered_page, autoru_catalogue_subset
from app.scraper.navigation_recovery import wait_for_navigation_recovery, watch_main_frame_documents

ALL = 'https://auto.ru/diler/lcv/all/a1_avto_moskva/'
USED = 'https://auto.ru/diler/lcv/used/a1_avto_moskva/'


@pytest.mark.parametrize('destination', [
    USED.replace('a1_avto_moskva', 'another_dealer'),
    USED.replace('/lcv/', '/cars/'),
    USED.replace('auto.ru', 'another.ru'),
    'https://auto.ru/cars/lcv/used/',
])
def test_redirect_cannot_change_seller_vehicle_category_or_host(destination):
    assert not autoru_catalogue_subset(ALL, destination)
    assert not _same_recovered_page(ALL, destination, destination)


def test_subset_redirect_cannot_drop_filters_or_expand_a_condition_request():
    assert not autoru_catalogue_subset(ALL + '?price_from=100', USED)
    assert not autoru_catalogue_subset(USED, ALL)
    assert not autoru_catalogue_subset(USED, USED.replace('/used/', '/new/'))


def test_condition_redirect_does_not_wait_for_operator(tmp_path, monkeypatch):
    class RedirectTab(FakeTab):
        async def goto(self, url, **kwargs):
            self.url = USED
            raise RuntimeError(EXECUTION_CONTEXT_DESTROYED)

    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        harness.scripted.append(RedirectTab())
        result = await asyncio.wait_for(harness.adapter().scan_filter(
            USED.replace('/used/', '/new/'), max_pages=2, seller_catalogue=True), timeout=1)
        assert not result.complete and not result.exhausted
        assert result.diagnostics['page_1_state'] == 'condition_redirect'
        assert 'another condition' in result.error
    asyncio.run(exercise())


def test_delayed_condition_redirect_exits_recovery_after_stable_document():
    async def exercise():
        tab = FakeTab(USED, catalogue(1))
        watcher = watch_main_frame_documents(tab)
        tab.deliver(tab.document())
        try:
            with pytest.raises(RuntimeError, match='another condition'):
                await asyncio.wait_for(wait_for_navigation_recovery(
                    tab, url=USED.replace('/used/', '/new/'), reopen=fail_reopen,
                    progress=None, page_number=1, request_timeout=1,
                    poll_seconds=0.01, source='auto_ru', document_watcher=watcher,
                    reject_destination=_condition_redirected), timeout=1)
        finally:
            watcher.detach()
    asyncio.run(exercise())


def test_already_loaded_condition_document_recovers_without_refresh():
    async def exercise():
        tab = FakeTab(USED, catalogue(1))
        watcher = watch_main_frame_documents(tab)
        tab.deliver_request(tab.navigation_request())
        document = tab.document()
        tab.deliver(document)
        try:
            page, response = await asyncio.wait_for(wait_for_navigation_recovery(
                tab, url=ALL, reopen=fail_reopen, progress=None, page_number=1,
                request_timeout=1, poll_seconds=0.01, source='auto_ru',
                document_watcher=watcher), timeout=1)
            assert page is tab and response is document
            assert tab.gotos == []
        finally:
            watcher.detach()
    asyncio.run(exercise())


@pytest.mark.parametrize('failed_complement', [False, True])
def test_all_catalogue_requires_both_conditions(tmp_path, monkeypatch, failed_complement):
    class ConditionTab(FakeTab):
        async def goto(self, url, **kwargs):
            self.url = USED if url == ALL else url
            self.html = ('<h1>Доступ ограничен</h1>' if failed_complement and '/new/' in url
                         else '<h1>1 предложение</h1>' + catalogue(2 if '/new/' in url else 1))
            return await super().goto(self.url, **kwargs)

    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, 'catalogue_lazy_load_enabled', False)
        monkeypatch.setattr(settings, 'captcha_operator_wait_seconds', 0)
        harness.scripted.extend([ConditionTab(), ConditionTab()])
        result = await harness.adapter().scan_filter(ALL, max_pages=2, seller_catalogue=True)
        assert result.complete is (not failed_complement)
        assert result.exhausted is (not failed_complement)
        assert len(result.hits) == (1 if failed_complement else 2)
        assert result.diagnostics['condition_runs'][1]['url'] == USED.replace('/used/', '/new/')
        if not failed_complement:
            assert [hit.page_number for hit in result.hits] == [1, 2]
            assert result.diagnostics['page_2_evidence']
    asyncio.run(exercise())


def test_wrong_dealer_document_explains_wait_and_never_resumes():
    async def exercise():
        wrong = USED.replace('a1_avto_moskva', 'another_dealer')
        tab = FakeTab(wrong, catalogue(1))
        watcher = watch_main_frame_documents(tab)
        tab.deliver(tab.document())
        events = []
        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=ALL, reopen=fail_reopen, progress=events.append, page_number=1,
            request_timeout=1, poll_seconds=0.01, source='auto_ru',
            document_watcher=watcher))
        try:
            await asyncio.sleep(0.05)
            assert not task.done()
            waiting = [event for event in events if event['event'] == 'navigation_operator_waiting']
            assert waiting[0]['wait_reason'] == 'wrong_destination'
            assert waiting[0]['document_url'] == waiting[0]['final_url'] == wrong
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            watcher.detach()
    asyncio.run(exercise())


@pytest.mark.parametrize('navigation_race', [False, True])
def test_all_to_used_preserves_positive_hits_and_incomplete_coverage(
        tmp_path, monkeypatch, navigation_race):
    class RedirectTab(FakeTab):
        def __init__(self):
            super().__init__('', '<h1>1 предложение</h1>' + catalogue(1))
            self.fault = navigation_race

        async def goto(self, url, **kwargs):
            self.gotos.append(url)
            self.url = USED
            self.deliver_request(self.navigation_request())
            response = self.document()
            self.deliver(response)
            return response

        async def evaluate(self, script):
            if script == 'window.scrollTo(0, 0)' and self.fault:
                self.fault = False
                raise RuntimeError(EXECUTION_CONTEXT_DESTROYED)
            return None

    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, 'catalogue_lazy_load_enabled', False)
        tab = RedirectTab()
        harness.scripted.append(tab)
        result = await asyncio.wait_for(harness.adapter().scan_filter(
            ALL, max_pages=1, seller_catalogue=True), timeout=2)
        assert tab.gotos == [ALL]
        assert len(result.hits) == 1
        assert result.page_count == 1
        assert not result.complete
        assert result.diagnostics['catalogue_scope_narrowed'] is True
        assert result.diagnostics['catalogue_condition_url'] == USED
        assert 'full inventory coverage unproven' in result.error
    asyncio.run(exercise())
