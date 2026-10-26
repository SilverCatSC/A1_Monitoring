import asyncio

from app.scraper.browser_session import CycleBrowserSession, browser_page, run_browser_task


class FakePage:
    def __init__(self):
        self.closed = False
        self.viewport = None
        self.brought_to_front = False

    async def set_viewport_size(self, viewport):
        self.viewport = viewport

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed

    async def bring_to_front(self):
        self.brought_to_front = True


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.pages = [self.page]
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True
        await self.page.close()


class FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]
        self.private_contexts = []
        self.closed = False

    async def new_context(self):
        context = FakeContext()
        self.private_contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.connected_to = None
        self.launched_headless = None

    async def connect_over_cdp(self, url):
        self.connected_to = url
        return self.browser

    async def launch(self, *, headless):
        self.launched_headless = headless
        return self.browser


class FakePlaywright:
    def __init__(self):
        context = FakeContext()
        self.browser = FakeBrowser(context)
        self.chromium = FakeChromium(self.browser)


def test_attached_local_browser_uses_fresh_private_context_and_closes_it(monkeypatch):
    monkeypatch.setattr(
        'app.scraper.browser_session.settings.browser_cdp_url', 'http://127.0.0.1:19222'
    )
    playwright = FakePlaywright()

    async def exercise():
        async with browser_page(playwright) as page:
            assert page.viewport == {'width': 1920, 'height': 1080}

    asyncio.run(exercise())

    assert playwright.chromium.connected_to == 'http://127.0.0.1:19222'
    assert playwright.browser.contexts[0].page.closed is False
    assert playwright.browser.private_contexts[0].page.brought_to_front is True
    assert playwright.browser.private_contexts[0].closed is True
    assert playwright.browser.closed is False


def test_attached_browser_does_not_reuse_existing_page(monkeypatch):
    monkeypatch.setattr(
        'app.scraper.browser_session.settings.browser_cdp_url', 'http://127.0.0.1:19222'
    )
    playwright = FakePlaywright()
    playwright.browser.contexts[0].pages = []

    async def exercise():
        async with browser_page(playwright):
            pass

    asyncio.run(exercise())
    assert playwright.browser.private_contexts[0].closed is True
    assert playwright.browser.contexts[0].page.closed is False


def test_managed_browser_is_closed(monkeypatch):
    monkeypatch.setattr('app.scraper.browser_session.settings.browser_cdp_url', None)
    monkeypatch.setattr('app.scraper.browser_session.settings.playwright_headless', True)
    playwright = FakePlaywright()

    async def exercise():
        async with browser_page(playwright):
            pass

    asyncio.run(exercise())

    assert playwright.chromium.launched_headless is True
    assert playwright.browser.private_contexts[0].closed is True
    assert playwright.browser.closed is True


def test_cycle_reuses_one_private_context_per_source_then_discards_it(monkeypatch):
    import app.scraper.browser_session as session_module

    monkeypatch.setattr(session_module.settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    contexts = []

    class Context:
        def __init__(self):
            self.pages = []
            self.closed = False

        async def new_page(self):
            page = FakePage()
            self.pages.append(page)
            return page

        async def close(self):
            self.closed = True
            for page in self.pages:
                await page.close()

    class Browser:
        async def new_context(self):
            context = Context()
            contexts.append(context)
            return context

    class Chromium:
        async def connect_over_cdp(self, _url):
            return Browser()

    class Playwright:
        chromium = Chromium()

        async def stop(self):
            self.stopped = True

    class Manager:
        async def start(self):
            return Playwright()

    monkeypatch.setattr(session_module, 'async_playwright', Manager)

    async def visit(source):
        async with browser_page(object(), source=source) as page:
            assert not page.closed
        return page.closed

    with CycleBrowserSession():
        assert not run_browser_task(visit('auto_ru'))
        assert not run_browser_task(visit('auto_ru'))
        assert not run_browser_task(visit('avito'))
        assert len(contexts) == 2
        assert len(contexts[0].pages) == 1
        assert len(contexts[1].pages) == 1
        assert all(not context.closed for context in contexts)

    assert all(context.closed for context in contexts)
    assert all(page.closed for context in contexts for page in context.pages)

    with CycleBrowserSession():
        assert not run_browser_task(visit('auto_ru'))
        assert len(contexts) == 3
        assert not contexts[2].closed
    assert contexts[2].closed


def test_cycle_replaces_closed_context_without_discarding_other_source():
    async def exercise():
        contexts = []

        class Context(FakeContext):
            async def new_page(self):
                if self.closed:
                    raise RuntimeError('Target page, context or browser has been closed')
                return await super().new_page()

        class Browser:
            def is_connected(self):
                return True

            async def new_context(self):
                context = Context()
                contexts.append(context)
                return context

        session = CycleBrowserSession()
        session._browser = Browser()
        session._playwright = object()
        first = await session.new_page('avito')
        other = await session.new_page('auto_ru')
        await contexts[0].close()
        replacement = await session.new_page('avito')
        assert replacement is not first and not replacement.closed
        assert await session.new_page('auto_ru') is other
        assert len(contexts) == 3
        for context in contexts:
            await context.close()

    asyncio.run(exercise())
