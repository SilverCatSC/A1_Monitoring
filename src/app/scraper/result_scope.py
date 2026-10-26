"""Visible result boundaries and pagination markers observed in the local browser."""
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

SUPPLEMENT_HEADING = re.compile(
    r'похожие (?:объявления|предложения|автомобили|на этот)|в друг(?:ом городе|их городах)|'
    r'ещ[её]\s+\d+\s+в\s+других\s+городах|'
    r'рекомендованн|рекомендуем|объявления из других', re.I)

# A seller profile tab counter counts only when one element's entire visible
# text is exactly the tab label plus a single number («Активные 8»).
SELLER_TAB_COUNTER = re.compile(r'^(активные|заверш[её]нные)\s+(\d{1,6})$', re.I)


def avito_model_search_scope_problem(requested_url, final_url):
    """Stop a Voyah search before a redirect turns it into a general catalogue."""
    requested = urlsplit(requested_url)
    if not re.match(r'^/moskva/avtomobili/(?:novyy/)?voyah/', requested.path):
        return None
    final = urlsplit(final_url)
    if final.scheme != 'https' or final.hostname not in {'avito.ru', 'www.avito.ru'}:
        return 'unexpected_destination'
    if final.path.rstrip('/') != requested.path.rstrip('/'):
        return 'model_filter_lost'
    query = parse_qs(final.query)
    expected = parse_qs(requested.query)
    if any(query.get(key) != expected.get(key) for key in ('radius', 'searchRadius')):
        return 'geography_filter_lost'
    priority = query.get('localPriority')
    expected_priority = expected.get('localPriority')
    # The operator's saved Dreamer page drops localPriority=0 while retaining
    # both zero-radius parameters and showing the local-priority switch off.
    # This cosmetic normalization must not stop the model search.
    if priority != expected_priority and not (priority is None and expected_priority == ['0']):
        return 'local_priority_changed'
    return None


def avito_catalogue_show_all_url(html, current_url):
    """Follow only Avito's observed full-inventory control for the same brand."""
    current = urlsplit(current_url)
    brand = re.fullmatch(r'/brands/([^/]+)/(?:all/avtomobili|items(?:/all/avtomobili)?)/?', current.path)
    if ((current.hostname or '').removeprefix('www.') != 'avito.ru' or not brand):
        return None
    soup = BeautifulSoup(html, 'html.parser')
    candidates = set()
    for node in soup.select('a[data-marker="item_list_with_filters/show_all_button"][href]'):
        if (node.has_attr('hidden') or node.get('aria-hidden') == 'true'
                or node.find_parent(['aside', 'template']) or node.find_parent(attrs={'hidden': True})):
            continue
        destination = urljoin(current_url, node['href'])
        parts = urlsplit(destination)
        query = parse_qs(parts.query, keep_blank_values=True)
        if ((parts.hostname or '').removeprefix('www.') == 'avito.ru'
                and parts.scheme == 'https'
                and parts.path.rstrip('/') == f'/brands/{brand[1]}/items'
                and query == {'s': ['profile_search_show_all']}
                and destination != current_url):
            candidates.add(destination)
    return next(iter(candidates)) if len(candidates) == 1 else None


def avito_active_catalogue_url(catalogue_url):
    """A retained active counter must not cross into a filtered/closed view."""
    parts = urlsplit(catalogue_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    return bool(
        parts.scheme == 'https'
        and (parts.hostname or '').removeprefix('www.') == 'avito.ru'
        and re.fullmatch(r'/brands/[^/]+/(?:items(?:/all/avtomobili)?|all/avtomobili)/?', parts.path)
        and not set(query) - {'src', 'iid', 'sellerId', 'p', 's', 'cursor', 'from_navigation'}
        and ('s' not in query or query['s'] == ['profile_search_show_all'])
    )


def avito_catalogue_result_count(html, catalogue_url):
    """Count only the visible primary profile-list header on an active route."""
    if not avito_active_catalogue_url(catalogue_url):
        return None
    soup = BeautifulSoup(html, 'html.parser')
    for node in reversed(soup.select('script,style,template,aside,[hidden],[aria-hidden="true"], '
                                    '[data-marker*="recommend"],[class*="Recommendations"]')):
        node.decompose()
    for node in reversed(soup.find_all(style=re.compile(r'display\s*:\s*none|visibility\s*:\s*hidden', re.I))):
        node.decompose()
    roots = soup.select('[data-marker="item_list_with_filters/filters"]')
    if len(roots) != 1:
        return None
    counts = set()
    for heading in roots[0].select('h1,h2,h3,h4,h5,h6,[role="heading"]'):
        text = ' '.join(heading.get_text(' ', strip=True).split())
        match = re.fullmatch(r'Найден[оы]?\s+(\d[\d\s]*)\s+объявлени[еяй]', text, re.I)
        if match:
            counts.add(int(re.sub(r'\D', '', match[1])))
    return next(iter(counts)) if len(counts) == 1 else None


def seller_catalogue_counters(html, catalogue_url=None):
    """Require a selected active tab paired with the completed tab in one bar.

    This is a conservative markup contract; external CSS and an unrecognised
    live tab layout still require verification, rather than guessing an end.
    """
    if catalogue_url is not None and not avito_active_catalogue_url(catalogue_url):
        return None
    soup = BeautifulSoup(html, 'html.parser')
    for node in reversed(soup.select(
        'template, [hidden], [aria-hidden="true"], noscript, script, style, '
        'aside, [data-marker*="recommend"], [class*="Recommendations"], '
        '[class*="RelatedOffers"], [data-marker="item"], [data-marker*="description"]'
    )):
        node.decompose()
    for node in reversed(soup.find_all(style=re.compile(
        r'display\s*:\s*none|visibility\s*:\s*hidden', re.I
    ))):
        node.decompose()

    def tab(node):
        marker = ' '.join(str(node.get(k, '')) for k in ('class', 'data-marker', 'id'))
        return node.get('role') == 'tab' or bool(re.search(r'tab', marker, re.I))

    def label(node):
        return SELLER_TAB_COUNTER.fullmatch(' '.join(node.get_text(' ', strip=True).split()))

    found = {'активные': set(), 'завершенные': set()}
    active_nodes = []
    for node in soup.find_all(True):
        match = label(node)
        if not match or not tab(node):
            continue
        key = match.group(1).casefold().replace('ё', 'е')
        found[key].add(int(match.group(2)))
        if key == 'активные':
            selected = node.get('aria-selected') == 'true' or bool(re.search(
                r'(?:^|[-_\s])(?:active|selected|current)(?:$|[-_\s])',
                ' '.join(node.get('class', [])), re.I
            ))
            siblings = node.parent.find_all(recursive=False) if node.parent else []
            completed_sibling = any(
                other is not node and tab(other) and label(other)
                and label(other).group(1).casefold().replace('ё', 'е') == 'завершенные'
                for other in siblings
            )
            if selected and completed_sibling:
                active_nodes.append(node)
    if any(len(values) > 1 for values in found.values()):
        return None
    active = next(iter(found['активные'])) if active_nodes else None
    completed = next(iter(found['завершенные'])) if found['завершенные'] else None
    return {'active': active, 'completed': completed} if active is not None or completed is not None else None


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
    # Group pages can put the recommendation heading before a list root.
    # A root selector must never pull those concrete (but unrelated) ads back
    # into the primary results. The model sidebar's generic heading is not
    # this explicit main-column boundary.
    document_order = {id(node): index for index, node in enumerate(soup.find_all(True))}
    recommendation_boundary = min((document_order[id(node)]
        for node in soup.select('h2,h3,[role="heading"], [class*="EmptyGroupRelated__title"]')
        if re.search(r'похожие на этот', node.get_text(' ', strip=True), re.I)),
        default=float('inf'))
    eligible_roots = [node for node in roots
                      if document_order[id(node)] < recommendation_boundary and node.select(selector)]
    if eligible_roots:
        root = max(eligible_roots, key=lambda node: len(node.select(selector)))
    ordered = {id(node): index for index, node in enumerate(root.find_all(True))}
    boundary = min((ordered[id(node)] for node in root.select('h2,h3,[role="heading"]')
                    if SUPPLEMENT_HEADING.search(node.get_text(' ', strip=True))), default=float('inf'))
    return [node for node in root.select(selector) if ordered.get(id(node), -1) < boundary
            and document_order[id(node)] < recommendation_boundary]


def _avito_pagination(html):
    """Read native page markers regardless of the profile pager's wrapper."""
    soup = BeautifulSoup(html, 'html.parser')
    for node in reversed(soup.select(
        'aside, template, [hidden], [aria-hidden="true"], '
        '[data-marker*="recommend"], [class*="Recommendations"], [class*="RelatedOffers"]'
    )):
        node.decompose()
    for node in reversed(soup.find_all(style=re.compile(
        r'display\s*:\s*none|visibility\s*:\s*hidden', re.I
    ))):
        node.decompose()
    pages = []
    selected = set()
    for node in soup.select('[data-marker^="pagination-button/page("]'):
        match = re.fullmatch(r'pagination-button/page\((\d+)\)', node.get('data-marker', ''))
        if not match:
            continue
        number = int(match[1])
        pages.append((number, node))
        classes = ' '.join(node.get('class', []))
        if (node.get('aria-current') in {'page', 'true'}
                or re.search(r'(?:^|[-_\s])(?:current|selected)(?:$|[-_\s])', classes)):
            selected.add(number)
    current = next(iter(selected)) if len(selected) == 1 else None

    def enabled(node):
        return (not node.has_attr('disabled') and node.get('aria-disabled') != 'true'
                and not re.search(r'(?:^|[-_\s])disabled(?:$|[-_\s])',
                                  ' '.join(node.get('class', []))))

    next_nodes = [node for node in soup.select('[data-marker="pagination-button/nextPage"][href]')
                  if enabled(node)]
    if not next_nodes and current is not None:
        next_nodes = [node for number, node in pages
                      if number == current + 1 and node.get('href') and enabled(node)]
    hrefs = {node['href'] for node in next_nodes}
    href = next(iter(hrefs)) if len(hrefs) == 1 else None
    numbers = [number for number, _ in pages]
    last = bool(current and numbers and current == max(numbers) and not next_nodes)
    return soup, current, last, bool(next_nodes), href


def pagination_state(html, source):
    soup = BeautifulSoup(html, 'html.parser')
    current = None
    total = None
    numbers = []
    if source == 'avito':
        soup, current, last, has_next, _ = _avito_pagination(html)
        counter = soup.select_one('[data-marker="page-title/count"]')
        if counter and re.fullmatch(r'[\d\s\u00a0]+', counter.get_text(strip=True)):
            total = int(re.sub(r'\D', '', counter.get_text()))
        return {'current': current, 'total': total, 'last': last, 'has_next': has_next}
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


def pagination_next_url(html, source, current_url):
    """Follow the marketplace's observed next link instead of inventing a URL."""
    if source == 'avito':
        _, current_page, _, _, href = _avito_pagination(html)
        if not href:
            return None
        destination = urljoin(current_url, href)
        current, target = urlsplit(current_url), urlsplit(destination)
        before = parse_qs(current.query, keep_blank_values=True)
        after = parse_qs(target.query, keep_blank_values=True)
        page = after.pop('p', [])
        previous = before.pop('p', ['1'])
        cursor = after.pop('cursor', [])
        previous_cursor = before.pop('cursor', [])
        # Profile pagination may drop attribution parameters. It may never
        # switch to the closed inventory or change a substantive filter.
        for key in ('src', 'iid', 'sellerId', 'from_navigation'):
            before.pop(key, None)
            after.pop(key, None)
        if before.get('s') == ['profile_search_show_all'] and 's' not in after:
            before.pop('s')
        try:
            number = int(page[0]) if len(page) == 1 else 0
            expected = (current_page or int(previous[0])) + 1
        except (ValueError, IndexError):
            return None
        if (target.scheme != 'https' or target.username or target.password
                or (target.hostname or '').removeprefix('www.') != 'avito.ru'
                or (current.hostname or '').removeprefix('www.') != 'avito.ru'
                or ':' in target.netloc
                or target.path.rstrip('/') != current.path.rstrip('/')
                or before != after or target.fragment):
            return None
        if page:
            if number != expected:
                return None
        elif not (len(cursor) == 1 and cursor[0] and cursor != previous_cursor):
            return None
        return destination
    soup = BeautifulSoup(html, 'html.parser')
    selector = ('[data-seo="listing-pagination"] .ListingPagination__next[href]:not(.Button_disabled), '
                '.ListingPagination .ListingPagination__next[href]:not(.Button_disabled)')
    node = soup.select_one(selector)
    href = node.get('href') if node else None
    return urljoin(current_url, href) if href else None
