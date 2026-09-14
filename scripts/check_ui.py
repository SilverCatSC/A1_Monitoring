"""Read-only local dashboard QA with isolated Chrome. No marketplace access or user profile."""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

if __package__:
    from .local_api import env_values, local_api_credentials, local_api_request
else:
    from local_api import env_values, local_api_credentials, local_api_request

ROOT = Path(__file__).resolve().parents[1]


async def main():
    values = env_values()
    base = f'http://127.0.0.1:{values.get("APP_BIND_PORT", "18000")}'
    try:
        # Validate the target before creating a context that may hold local
        # Basic-auth credentials. The test must never navigate to a remote URL.
        local_api_request(base + '/api/v1/ready')
        credentials = local_api_credentials(values)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f'UI_CHECK_REFUSED reason={exc}') from exc
    output = ROOT / 'artifacts' / 'ui_0_8'
    output.mkdir(parents=True, exist_ok=True)
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            context_options = {}
            if credentials is not None:
                context_options['http_credentials'] = {
                    'username': credentials[0],
                    'password': credentials[1],
                    'origin': base,
                }
            context = await browser.new_context(**context_options)
            async def local_only(route):
                if urlsplit(route.request.url).netloc == urlsplit(base).netloc:
                    await route.continue_()
                else:
                    await route.abort()
            await context.route('**/*', local_only)
            page = await context.new_page()
            page.on('pageerror', lambda error: failures.append(str(error)))
            for width in (1440, 390):
                await page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
                for section in ('', '/listings', '/reconciliation', '/placements', '/analytics', '/history', '/feedback', '/activity', '/settings'):
                    response = await page.goto(base + '/api/v1/dashboard' + section, wait_until='networkidle')
                    assert response.status == 200, (section, response.status)
                    assert await page.locator('.app-nav nav a[aria-current="page"]').count() == 1, section
                    overflow = await page.evaluate('document.documentElement.scrollWidth > innerWidth + 2')
                    if overflow:
                        failures.append(f'Horizontal overflow: {width} {section or "overview"}')
                    await page.screenshot(path=output / f'{section.strip("/") or "overview"}_{width}.png', full_page=True)
                    print(f'UI {width}px {section or "overview"} · HTTP 200', flush=True)
            await page.goto(base + '/api/v1/dashboard/listings', wait_until='networkidle')
            detail = page.locator('a[href^="/api/v1/dashboard/listings/"]').first
            if await detail.count():
                await detail.click()
                await page.wait_for_load_state('networkidle')
                if await page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'):
                    failures.append('Horizontal overflow: listing detail 390')
                await page.screenshot(path=output / 'detail_390.png', full_page=True)
            await page.goto(base + '/api/v1/dashboard?days=30&source=avito', wait_until='networkidle')
            await page.get_by_text('Все размещения →', exact=True).click()
            await page.wait_for_load_state('networkidle')
            selected = parse_qs(urlsplit(page.url).query)
            assert selected['days'] == ['30'] and selected['source'] == ['avito']
            print('UI drill-down · period and marketplace preserved', flush=True)
            # UI-only fixtures: no database writes, no artificial monitoring observations.
            state = {'status': 'preparing', 'updated_at': datetime.now(UTC).isoformat(),
                     'total_filters': 0, 'completed_filters': 0, 'events': []}
            await page.route('**/status/scans/progress',
                             lambda route: route.fulfill(content_type='application/json', body=json.dumps(state)))
            await page.goto(base + '/api/v1/dashboard', wait_until='networkidle')
            assert await page.locator('#scan-progress-status').inner_text() == 'Обновление реестра'
            assert await page.locator('[role="progressbar"]').get_attribute('aria-valuenow') == '0'
            state.update(status='reconciling')
            await page.reload(wait_until='networkidle')
            assert await page.locator('#scan-progress-status').inner_text() == 'Сверка ссылок продавца'
            state.update(status='failed', error='Контрольный сбой импорта')
            await page.reload(wait_until='networkidle')
            assert await page.locator('#scan-progress-current').inner_text() == 'Контрольный сбой импорта'
            print('UI progress preparing/reconciling/failed · isolated browser fixtures only', flush=True)
            # Render a populated confirmation screen without inserting fake cars into the registry.
            candidate = {'url': 'https://auto.ru/cars/used/sale/mercedes/vle/1000000000-example/',
                         'title': 'ТЕСТОВЫЙ КАНДИДАТ · Mercedes-Benz VLE', 'price': '23 850 000 ₽',
                         'basis': 'Фикстура интерфейса, не реальное объявление'}
            record = SimpleNamespace(id='ui-fixture', listing_id='ui-fixture', source=SimpleNamespace(value='auto_ru'),
                                     state='review_required', reason='Тест: старая ссылка не найдена', candidates=[candidate])
            data = {'issues': 1, 'total': 1, 'last_time': '09.09.2026 12:00', 'sources': {},
                    'rows': [{'record': record, 'name': 'ТЕСТОВЫЙ АВТОМОБИЛЬ · VLE', 'vin': 'VIN не указан',
                              'time': '09.09.2026 12:00', 'label': 'Нужно проверить ссылку',
                              'changed': False, 'current_url': candidate['url'], 'override': None}]}
            env = Environment(loader=FileSystemLoader(ROOT / 'src/app/templates'), autoescape=select_autoescape(['html']))
            html = env.get_template('reconciliation.html').render(data=data)
            await page.route('**/dashboard/reconciliation', lambda route: route.fulfill(content_type='text/html', body=html))
            for width in (1440, 390):
                await page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
                await page.goto(base + '/api/v1/dashboard/reconciliation', wait_until='networkidle')
                await page.get_by_text('Кандидаты на замену · 1', exact=True).click()
                assert not await page.locator('form').evaluate('(form) => form.checkValidity()')
                if await page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'):
                    failures.append(f'Confirmation form overflow {width}')
                await page.evaluate('window.scrollTo(0, 0)')
                await page.screenshot(path=output / f'reconciliation_fixture_{width}.png', full_page=True)
            requests = []
            async def mock_confirmation(route):
                requests.append(route.request.post_data_json)
                await route.fulfill(status=409, content_type='application/json', body=json.dumps({'detail': 'Тест: сверка устарела'}))
            await page.route('**/dealer/reconciliation/ui-fixture/confirm', mock_confirmation)
            await page.locator('[name="url"]').select_option(candidate['url'])
            await page.locator('[name="actor"]').fill('Тестовый оператор')
            await page.locator('[name="reason"]').fill('Проверка формы без сохранения в БД')
            await page.locator('[type="checkbox"]').check()
            await page.get_by_role('button', name='Сохранить подтверждённую связь').click()
            await page.get_by_text('Тест: сверка устарела', exact=True).wait_for()
            assert requests[0]['url'] == candidate['url']
            assert await page.locator('form button').is_enabled()
            print('UI confirmation fields and stale-check rejection · mock response, no DB writes', flush=True)
        finally:
            await browser.close()
    if failures:
        raise SystemExit('\n'.join(failures))
    print(f'UI_OK · {output}')


if __name__ == '__main__':
    asyncio.run(main())
