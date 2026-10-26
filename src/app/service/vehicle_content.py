"""Compare recorded vehicle attributes without using them as ad identity."""

import re


def vin_values(value):
    return frozenset(re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', str(value or '').upper()))


def vins_conflict(registry_value, card_value):
    """A card VIN may match one of several documented VINs for one vehicle.

    Missing or malformed values do not establish a mismatch. This comparison
    never creates a unique_id binding or assigns a publication to a vehicle.
    """
    registry, card = vin_values(registry_value), vin_values(card_value)
    return bool(registry and card and registry.isdisjoint(card))


def structured_vehicle_fields(html):
    """Explicit parameter rows only; no identity joins or description guesses."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    for node in reversed(
        soup.select(
            'script,style,template,aside,[hidden],[aria-hidden="true"], '
            '[class*="Description"],[data-marker*="description"], '
            '[class*="Recommendations"],[class*="RelatedOffers"]'
        )
    ):
        node.decompose()
    for node in reversed(
        soup.find_all(style=re.compile(r'display\s*:\s*none|visibility\s*:\s*hidden', re.I))
    ):
        node.decompose()
    labels = {
        'год выпуска': 'year_attribute',
        'пробег': 'mileage_raw',
        'владельцы': 'owners_raw',
        'количество владельцев': 'owners_raw',
        'комплектация': 'factory_equipment',
        'ндс': 'vat_attribute',
    }
    values = {}
    rows = soup.select(
        '[class*="CardInfoSummarySimpleRow-"], [class*="CardInfoRow-"], '
        '.CardInfoRow, [data-marker="item-view/item-params"] li'
    )
    for row in rows:
        cells = row.find_all(recursive=False)
        text = ' '.join(row.get_text(' ', strip=True).split())
        label = ' '.join(cells[0].get_text(' ', strip=True).split()).rstrip(':').casefold() if cells else ''
        value = ' '.join(' '.join(n.get_text(' ', strip=True).split()) for n in cells[1:])
        if label not in labels or not value:
            match = re.fullmatch(r'([^:]+):\s*(.+)', text)
            if not match:
                continue
            label, value = match[1].strip().casefold(), match[2].strip()
        if label in labels and value:
            values.setdefault(labels[label], set()).add(value)
    result = {key: next(iter(v)) if len(v) == 1 else None for key, v in values.items()}
    result['attribute_ambiguities'] = sorted(k for k, v in values.items() if len(v) > 1)
    result['field_sources'] = {
        k: {'kind': 'structured_parameter', 'values': sorted(v)} for k, v in values.items()
    }
    raw_year = result.get('year_attribute') or ''
    result['year_attribute'] = int(raw_year) if re.fullmatch(r'(?:19|20)\d{2}', raw_year) else None
    mileage = result.get('mileage_raw') or ''
    result['mileage_km'] = (
        int(re.sub(r'\D', '', mileage))
        if re.fullmatch(r'\d[\d\s\u00a0\u202f]*(?:\s*(?:км|km))?', mileage, re.I)
        else None
    )
    owners = result.get('owners_raw') or ''
    exact = re.fullmatch(r'(\d+)(?:\s+владел(?:ец|ьца|ьцев))?', owners, re.I)
    minimum = re.fullmatch(r'(\d+)\s+и\s+более', owners, re.I)
    result['owners_count'] = int(exact[1]) if exact else None
    result['owners_minimum'] = int(minimum[1]) if minimum else result['owners_count']
    return result
