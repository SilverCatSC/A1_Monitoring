"""Read saved content evidence without visiting or changing a marketplace."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.models import EngineType
from app.scraper.base import is_marketplace_listing_url

_RUSSIAN_VISUALS = str.maketrans(dict(zip(
    'AaBCcEeHKkMOoPpTXxy', 'АаВСсЕеНКкМОоРрТХху', strict=True)))
_LATIN_VISUALS = str.maketrans(dict(zip(
    'АаВвЕеКкМмНнОоРрСсТтУуХх', 'AaBbEeKkMmHhOoPpCcTtYyXx', strict=True)))


def content_claims(text):
    """Normalize visual aliases only for text analysis, never for identity joins."""
    original = str(text or '')
    folded = original.translate(_RUSSIAN_VISUALS).casefold().replace('ё', 'е')
    years = sorted({int(v) for v in re.findall(r'год\s+выпуска\s*:\s*(20\d{2})', folded)})
    years += [int(v) for v in re.findall(r'\b(20\d{2})\s+года?\s+выпуска', folded)
              if int(v) not in years]
    mileage = sorted({int(re.sub(r'\D', '', v)) for v in re.findall(
        r'\bпробег(?:ом)?(?:\s+в)?\s*[:—-]?\s*(\d[\d\s\u00a0]*?)\s*(?:км|километр)', folded)})
    without = bool(re.search(r'\bбез\s+ндс\b|\bне\s+облагается\s+ндс\b', folded))
    with_vat = bool(re.search(r'\bс\s+(?:полным\s+)?ндс\b|\bндс\s+включен\b', folded))
    vat = 'options' if without and with_vat else 'without' if without else 'with' if with_vat else None
    latin = original.translate(_LATIN_VISUALS).casefold()
    families = [name for name in ('SOLO-VISION', 'V-VIP GUARD', 'Comfort GUARD', 'Business Jet',
                                 'Chairman', 'V-VIP', 'SOLO', 'VIP')
                if re.search(r'(?<!\w)' + re.escape(name.casefold()).replace(r'\ ', r'\s*')
                             + r'(?!\w)', latin)]
    if 'V-VIP GUARD' in families:
        families = [name for name in families if name not in ('V-VIP', 'VIP')]
    elif 'V-VIP' in families:
        families = [name for name in families if name != 'VIP']
    if 'SOLO-VISION' in families:
        families = [name for name in families if name != 'SOLO']
    prefix = re.split(r'—{3,}', folded, maxsplit=1)[0]
    generic = ('мы не используем ии' in folded or
               ('—' * 3 in folded and 'id:' in prefix and not years and not mileage
                and not families and len(prefix) < 240))
    return {'description_years': years, 'mileage_km': mileage,
            'without_mileage': bool(re.search(r'без\s+пробега', folded)),
            'vat': vat, 'custom_packages': families, 'generic_description': generic,
            'powertrain_in_equipment_label': bool(re.search(
                r'комплектация\s*:\s*последовательный\s+гибрид', folded))}


def read_content_review(report, evidence_dir):
    """Accept only a hash-bound, cycle-owned review; no source or browser access."""
    reference = (report.get('business_review') or {}).get('content_review')
    if not isinstance(reference, dict) or not isinstance(reference.get('report_path'), str):
        return None
    relative = Path(reference['report_path'])
    parent = Path('cycles') / report['cycle_id'] / 'content_review'
    if relative.is_absolute() or relative.parent != parent:
        return None
    root = Path(evidence_dir).resolve()
    if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
        return None
    target = root / relative
    try:
        if not target.is_file() or target.stat().st_size > 4 * 1024 * 1024:
            return None
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference.get('sha256'):
            return None
        review = json.loads(raw)
        if (review.get('schema_version') != 1 or review.get('cycle_id') != report['cycle_id']
                or review.get('new_live_scan') is not False or review.get('source_writes') is not False
                or not isinstance(review.get('cards'), list) or len(review['cards']) > 1000
                or not isinstance(review.get('summary'), dict)):
            return None
        for card in review['cards']:
            if (not isinstance(card, dict) or card.get('source') not in ('avito', 'auto_ru')
                    or not is_marketplace_listing_url(EngineType(card['source']), card.get('url'))
                    or not isinstance(card.get('fields'), list) or len(card['fields']) != 6
                    or not isinstance(card.get('findings'), list)):
                return None
            for field in card['fields']:
                if any(not isinstance(field.get(key), str) for key in ('label', 'observed', 'reference', 'verdict')):
                    return None
        investigation = review.get('autoru_missing_ids')
        if investigation is not None:
            if (not isinstance(investigation, dict) or not isinstance(investigation.get('cases'), list)
                    or len(investigation['cases']) > 1000):
                return None
            for case in investigation['cases']:
                if (not isinstance(case, dict) or not is_marketplace_listing_url(EngineType.AUTO_RU, case.get('url'))
                        or not isinstance(case.get('full_description'), str)
                        or case.get('expected_id_is_publication_binding') is not False):
                    return None
        return review
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None
