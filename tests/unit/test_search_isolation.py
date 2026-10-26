"""Real Chrome/CDP: search cannot inherit operator or inspection storage/cache."""
import asyncio
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.async_api import async_playwright

from app.config import settings
from app.scraper.browser_session import (
    CycleBrowserSession,
    browser_page,
    run_browser_task,
    search_browser_session,
)
from scripts.local_scan import _chrome_executable


def test_private_search_context_isolated_and_captcha_state_survives_pages(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        assets = 0

        def do_GET(self):
            self.send_response(200)
            if self.path == '/asset':
                Handler.assets += 1
                body = f'asset-{Handler.assets}'.encode()
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.send_header('Content-Type', 'text/plain')
            else:
                body = b'<script>window.asset = fetch("/asset").then(r => r.text());</script>'
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/probe'
    profile = tmp_path / 'chrome'
    process = subprocess.Popen([
        _chrome_executable(), '--headless=new', '--remote-debugging-port=0',
        f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
        '--disable-extensions', 'about:blank',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = profile / 'DevToolsActivePort'
        for _ in range(100):
            if port_file.exists():
                break
            time.sleep(0.1)
        assert port_file.exists(), 'test Chrome CDP did not become ready'
        cdp = 'http://127.0.0.1:' + port_file.read_text().splitlines()[0]
        monkeypatch.setattr(settings, 'browser_cdp_url', cdp)

        async def state(page, label=None):
            await page.goto(url)
            result = await page.evaluate('''async () => ({
                cookie: document.cookie, stored: localStorage.getItem('marker'), asset: await window.asset
            })''')
            if label:
                await page.evaluate('''label => {
                    document.cookie = 'marker=' + label;
                    localStorage.setItem('marker', label);
                }''', label)
            return result

        async def personal(label=None):
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(cdp)
                page = await browser.contexts[0].new_page()
                try:
                    return await state(page, label)
                finally:
                    await page.close()

        async def visit(label=None):
            async with browser_page(None, source='auto_ru') as page:
                # CDP's disposable browser contexts are distinct from the
                # persistent default profile, irrespective of window styling.
                cdp_session = await page.context.new_cdp_session(page)
                info = await cdp_session.send('Target.getTargetInfo')
                assert info['targetInfo'].get('browserContextId')
                await cdp_session.detach()
                return await state(page, label)

        operator = asyncio.run(personal('personal'))
        with CycleBrowserSession():
            inspection = run_browser_task(visit('inspection'))
            assert inspection['cookie'] == '' and inspection['stored'] is None
            assert inspection['asset'] != operator['asset']
            with search_browser_session():
                search = run_browser_task(visit('captcha_passed'))
                assert search['cookie'] == '' and search['stored'] is None
                assert search['asset'] != inspection['asset']
                following_page = run_browser_task(visit())
                assert following_page['stored'] == 'captcha_passed'
                assert following_page['cookie'] == 'marker=captcha_passed'
                assert following_page['asset'] == search['asset']
            assert run_browser_task(visit())['stored'] == 'inspection'
        with CycleBrowserSession(), search_browser_session():
            next_cycle = run_browser_task(visit())
            assert next_cycle['stored'] is None and next_cycle['cookie'] == ''
            assert next_cycle['asset'] != search['asset']
        operator_after = asyncio.run(personal())
        assert operator_after['stored'] == 'personal'
        assert operator_after['asset'] == operator['asset']
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
