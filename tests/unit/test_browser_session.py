import asyncio

from app.scraper.browser_session import browser_page


class FakePage:
    def __init__(self):
        self.closed = False
        self.viewport = None
        self.brought_to_front = False

    async def set_viewport_size(self, viewport):
        self.viewport = viewport

    async def close(self):
        self.closed = True

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
