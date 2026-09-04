#!/usr/bin/env python3
"""Print a compact, non-mutating browser probe for scraper diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


async def probe(url: str, timeout_seconds: int) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1920, 'height': 1080})
        response = await page.goto(
            url,
            wait_until='domcontentloaded',
            timeout=timeout_seconds * 1000,
        )
        await page.wait_for_timeout(1500)
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        links = [
            str(anchor.get('href'))
            for anchor in soup.select('a[href]')
            if '/cars/' in str(anchor.get('href')) or '/avtomobili/' in str(anchor.get('href'))
        ][:30]
        sale_links = []
        for anchor in soup.select('a[href]'):
            href = str(anchor.get('href'))
            if not re.search(r'/cars/(?:used|new)/sale/', href):
                continue
            parent = anchor.find_parent(['article', 'section', 'div'])
            sale_links.append(
                {
                    'href': href,
                    'anchor_class': anchor.get('class'),
                    'parent_name': parent.name if parent else None,
                    'parent_class': parent.get('class') if parent else None,
                    'data_attrs': {
                        key: value
                        for key, value in (parent.attrs.items() if parent else [])
                        if str(key).startswith('data-')
                    },
                    'ancestor_classes': [
                        {'name': ancestor.name, 'class': ancestor.get('class')}
                        for ancestor in anchor.find_parents(limit=7)
                    ],
                }
            )
            if len(sale_links) >= 10:
                break
        payload = {
            'status': response.status if response else None,
            'title': await page.title(),
            'final_url': page.url,
            'html_length': len(html),
            'visible_text_sample': soup.get_text(' ', strip=True)[:1200],
            'candidate_links': links,
            'sale_links': sale_links,
            'vin_candidates': sorted(
                set(re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', html.upper()))
            )[:30],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--timeout', type=int, default=25)
    args = parser.parse_args()
    asyncio.run(probe(args.url, args.timeout))


if __name__ == '__main__':
    main()
