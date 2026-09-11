"""Fail-closed geography checks using visible controls, never listing text."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.models import EngineType


def url_matches_moscow(source: EngineType, url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    domain = 'auto.ru' if source == EngineType.AUTO_RU else 'avito.ru'
    if host not in (domain, 'www.' + domain) or not parts.path.startswith('/moskva/'):
        return False
    query = parse_qs(parts.query)
    if source == EngineType.AUTO_RU:
        return all(query.get(key) == [value] for key, value in {'geo_radius': '0', 'rid': '213'}.items())
    # Avito removes localPriority=0 from the canonical URL because zero is its
    # default state. An explicit non-zero value is still rejected.
    return (
        query.get('radius') == ['0']
        and query.get('searchRadius') == ['0']
        and query.get('localPriority') in (None, ['0'])
    )


def confirmed_control_text(text: str) -> bool:
    normalized = ' '.join(text.lower().replace('\u00a0', ' ').split())
    radii = re.findall(r'(?<!\d)(\d+)\s*км\b', normalized)
    return bool(re.search(r'\bмосква\b', normalized)) and bool(radii) and all(value == '0' for value in radii)


async def verify_geography(page, source: EngineType, allow_correction: bool = True) -> dict:
    """Unknown controls stop the filter. A visible radius slider can be corrected once."""
    controls = page.get_by_role('button', name=re.compile(r'Москва|радиус|местоположение', re.I))
    labels = []
    for index in range(await controls.count()):
        control = controls.nth(index)
        if await control.is_visible():
            labels.append(await control.inner_text())
    result = {'state': 'unverified', 'url': page.url, 'controls': labels, 'corrected': False}
    if source == EngineType.AUTO_RU:
        panel = page.get_by_test_id('header-geo-content')
        if not await panel.is_visible():
            opener = page.get_by_role('button', name='Москва', exact=True)
            if await opener.count() == 1 and await opener.is_visible():
                await opener.click()
        if await panel.is_visible():
            city = panel.locator('button[value]')
            slider = panel.get_by_role('slider')
            if await city.count() == 1 and await city.get_attribute('value') == '213' and await slider.count() == 1:
                radius = await slider.get_attribute('aria-valuenow')
                result['radius_before'] = radius
                if radius != '0' and allow_correction:
                    zero = panel.get_by_role('button', name='0', exact=True)
                    if await zero.count() == 1:
                        await zero.click()
                        save = panel.get_by_role('button', name='Сохранить', exact=True)
                        if await save.count() == 1 and await save.is_enabled():
                            await save.click()
                            return {**await verify_geography(page, source, False), 'corrected': True, 'radius_before': radius}
                if radius == '0' and url_matches_moscow(source, page.url):
                    from app.config import settings
                    from app.scraper.base import capture_page_evidence
                    result['evidence'] = await capture_page_evidence(page, source=source, search_url=page.url,
                                                                   page_number=0, evidence_dir=settings.evidence_dir)
                    result['state'] = 'verified'
                    await page.keyboard.press('Escape')
                    return result
    if source == EngineType.AVITO:
        visible_moscow = any(re.fullmatch(r'\s*Москва\s*', label, re.I) for label in labels)
        if url_matches_moscow(source, page.url) and visible_moscow:
            from app.config import settings
            from app.scraper.base import capture_page_evidence
            result['evidence'] = await capture_page_evidence(
                page, source=source, search_url=page.url, page_number=0,
                evidence_dir=settings.evidence_dir,
            )
            result['state'] = 'verified'
            return result
    if url_matches_moscow(source, page.url) and any(confirmed_control_text(label) for label in labels):
        result['state'] = 'verified'
        return result
    if not allow_correction:
        result['reason'] = 'Повторная проверка географии не подтвердила Москва, 0 км'
        return result
    # Open only a uniquely identified location control. Unknown UI is not guessed.
    location = page.get_by_role('button', name=re.compile(r'^Москва(?:\s*[+,].*)?$', re.I))
    if await location.count() == 1 and await location.is_visible():
        await location.click()
    # Only operate on a uniquely labelled radius slider, not an arbitrary slider.
    sliders = page.get_by_role('slider', name=re.compile(r'радиус', re.I))
    if await sliders.count() == 1 and await sliders.is_visible():
        minimum = await sliders.get_attribute('aria-valuemin')
        if minimum == '0':
            await sliders.focus()
            await sliders.press('Home')
            result['corrected'] = True
            dialogs = page.get_by_role('dialog')
            if await dialogs.count() == 1:
                apply = dialogs.get_by_role('button', name=re.compile(r'^(Применить|Сохранить|Показать.*)$', re.I))
                if await apply.count() == 1 and await apply.is_visible():
                    await apply.click()
                    checked = await verify_geography(page, source, allow_correction=False)
                    checked['corrected'] = True
                    checked['before_controls'] = labels
                    return checked
            result['reason'] = 'Радиус изменён; применение и повторное подтверждение недоступны'
    result.setdefault('reason', 'Москва и радиус 0 км не подтверждены видимым элементом географии')
    return result
