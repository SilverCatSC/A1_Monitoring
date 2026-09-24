"""Strict contract for the owner-defined 22-character placement identifier.

The identifier describes one publication/sale placement, not the physical
vehicle.  A new sale may legitimately receive a new placement ID for the same
VIN or inventory vehicle.  Never use it alone to infer a republication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_PLACEMENT_ID_RE = re.compile(
    r'^(?P<brand_model>[A-Z0-9]{4})'
    r'(?P<vehicle_type>0[0-9]{3})'
    r'(?P<vehicle_year>[0-9]{4})'
    r'(?P<placement_date>[0-9]{6})'
    r'(?P<ordinal>[0-9]{4})$'
)

# A placement ID in an advert is trusted only when the publisher explicitly
# labels it. Looking for a bare 22-character token in a marketing description
# would create a false identity assertion from arbitrary copy or a VIN-like ID.
_DESCRIPTION_PLACEMENT_ID_RE = re.compile(
    r'(?i)(?:\ba1\s*)?\b(?:идентификатор|placement\s*id|id|ид)'
    r'(?:\s*[-№#]?\s*(?:номер|number))?(?:\s*[:№#-]\s*|\s+)'
    r'(?P<placement_id>\w{1,64})\b'
)

_CYRILLIC_VISUAL_ALIASES = {
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H',
    'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'У': 'Y', 'Х': 'X',
}


class PlacementIdError(ValueError):
    """Raised when an identifier does not meet the placement-ID contract."""


@dataclass(frozen=True)
class PlacementIdentity:
    """Parsed publication metadata; this is deliberately not vehicle identity."""

    value: str
    brand_model_code: str
    vehicle_type_code: str
    vehicle_year: int
    placement_date: date
    ordinal: int


def parse_placement_id(value: str) -> PlacementIdentity:
    """Parse one exact 22-character ID without guessing brand/model codes.

    ``vehicle_year`` and ``placement_date.year`` are intentionally independent:
    a used 2024 vehicle can be placed in 2026.  The first token must already be
    supplied by the owner-approved brand/model dictionary; transliteration and
    acronym inference are outside this strict contract.
    """
    normalized = value.strip().upper()
    match = _PLACEMENT_ID_RE.fullmatch(normalized)
    if match is None:
        raise PlacementIdError('placement_id must match AAAA0BBBYYYYDDMMYYNNNN')

    try:
        placement_date = datetime.strptime(match['placement_date'], '%d%m%y').date()
    except ValueError as exc:
        raise PlacementIdError('placement_id has an invalid placement date') from exc

    vehicle_year = int(match['vehicle_year'])
    ordinal = int(match['ordinal'])
    if not 1886 <= vehicle_year <= 9999:
        raise PlacementIdError('placement_id has an invalid vehicle year')
    if ordinal < 1:
        raise PlacementIdError('placement_id ordinal must be between 0001 and 9999')

    return PlacementIdentity(
        value=normalized,
        brand_model_code=match['brand_model'],
        vehicle_type_code=match['vehicle_type'],
        vehicle_year=vehicle_year,
        placement_date=placement_date,
        ordinal=ordinal,
    )


def format_placement_id(
    *,
    brand_model_code: str,
    vehicle_type_code: str,
    vehicle_year: int,
    placement_date: date,
    ordinal: int,
) -> str:
    """Build one placement ID and validate it through the same parser."""
    if not 1 <= ordinal <= 9999:
        raise PlacementIdError('placement_id ordinal must be between 0001 and 9999')
    candidate = (
        f'{brand_model_code.strip().upper()}{vehicle_type_code.strip()}'
        f'{vehicle_year:04d}{placement_date.strftime("%d%m%y")}{ordinal:04d}'
    )
    return parse_placement_id(candidate).value


def placement_id_claim_from_description(description: str | None) -> str | None:
    """Read a labelled claim, including malformed length or Unicode text."""
    match = _DESCRIPTION_PLACEMENT_ID_RE.search(str(description or ''))
    return match['placement_id'] if match is not None else None


def visual_ascii_placement_candidate(raw_claim: str) -> str | None:
    """Suggest an ASCII ID for review; never validate an advert by this alone.

    Only visual Cyrillic aliases in the four-character brand/model prefix are
    considered. Numeric fields are never transliterated or repaired.
    """
    normalized = raw_claim.strip().upper()
    if len(normalized) != 22:
        return None
    prefix = ''.join(_CYRILLIC_VISUAL_ALIASES.get(char, char) for char in normalized[:4])
    candidate = prefix + normalized[4:]
    if candidate == normalized:
        return None
    try:
        return parse_placement_id(candidate).value
    except PlacementIdError:
        return None


def placement_id_from_description(description: str | None) -> str | None:
    """Return an explicitly labelled valid ASCII placement ID from a description.

    The marketplace URL, title and an unlabelled 22-character fragment are not
    identity evidence. A value such as ``A1 ID: MBVC011220262508260027`` in
    the description is. Malformed labels fail closed as ``None``; callers
    should surface them for marketing correction rather than infer a match.
    """
    claim = placement_id_claim_from_description(description)
    if claim is None:
        return None
    try:
        return parse_placement_id(claim).value
    except PlacementIdError:
        return None
