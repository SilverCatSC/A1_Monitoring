"""Visible result boundaries and pagination markers observed in the local browser."""
import re

from bs4 import BeautifulSoup

SUPPLEMENT_HEADING = re.compile(
    r'похожие (?:объявления|предложения|автомобили)|в друг(?:ом городе|их городах)|'
    r'рекомендованн|рекомендуем|объявления из других', re.I)


def primary_cards(html, selector, root_selector=None):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('aside, template, [hidden], [aria-hidden="true"], '
                            '[data-marker*="recommend"], [class*="Recommendations"], [class*="RelatedOffers"]'):
        node.decompose()
    roots = soup.select(root_selector) if root_selector else []
    # A catalogue may contain both a model summary and a concrete-offer group.
    # Taking the first matching root can silently discard the latter when the
    # summary appears first. The approved root with the most candidate cards is
    # the only useful result scope; ties preserve document order.
    root = max(roots, key=lambda node: len(node.select(selector)), default=soup)
    # Avito's hydrated page can retain an empty catalogue marker while
    # rendering the visible cards next to it.  An empty approved root is not
    # a result scope: fall back to the already-cleaned document and preserve
    # the normal supplemental-content boundary below.
    if roots and not root.select(selector):
        root = soup
    ordered = {id(node): index for index, node in enumerate(root.find_all(True))}
    boundary = min((ordered[id(node)] for node in root.select('h2,h3,[role="heading"]')
                    if SUPPLEMENT_HEADING.search(node.get_text(' ', strip=True))), default=float('inf'))
    return [node for node in root.select(selector) if ordered.get(id(node), -1) < boundary]


def pagination_state(html, source):
    soup = BeautifulSoup(html, 'html.parser')
    current = None
    total = None
    numbers = []
    if source == 'avito':
        counter = soup.select_one('[data-marker="page-title/count"]')
        if counter and re.fullmatch(r'[\d\s\u00a0]+', counter.get_text(strip=True)):
            total = int(re.sub(r'\D', '', counter.get_text()))
        pager = soup.select_one('[data-marker="pagination-button"]')
        if pager:
            for node in pager.select('[data-marker^="pagination-button/page("]'):
                match = re.fullmatch(r'pagination-button/page\((\d+)\)', node.get('data-marker', ''))
                if match:
                    number = int(match[1])
                    numbers.append(number)
                    if any('item_current-' in cls for cls in node.get('class', [])):
                        current = number
            next_node = pager.select_one('[data-marker="pagination-button/nextPage"][href]')
        else:
            next_node = None
    else:
        pager = soup.select_one('[data-seo="listing-pagination"], .ListingPagination')
        if pager:
            for node in pager.select('.ListingPagination__page'):
                label = node.get_text(strip=True)
                if label.isdigit():
                    numbers.append(int(label))
                    if 'Button_checked' in node.get('class', []):
                        current = int(label)
            next_node = pager.select_one('.ListingPagination__next[href]:not(.Button_disabled)')
        else:
            next_node = None
    last = bool(pager and current and numbers and max(numbers) == current and next_node is None)
    return {'current': current, 'total': total, 'last': last, 'has_next': next_node is not None}
