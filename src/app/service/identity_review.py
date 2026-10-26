"""Owner-confirmed ID review from saved observations; never opens a browser.

Price is corroboration, not an identifier. Confirmation requires the exact
unique feed ID, unique AvitoId, marketing URL, intact card evidence and the
owner's explicit review. Raw card claims and original cycle files are retained.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from app.models import EngineType, Listing, ListingPlacementIdentity, MonitoringCycle
from app.scraper.base import canonical_listing_key
from app.service.marketplace_placement_reconciliation import OpenedMarketplaceCard, reconcile_avito_placements
from app.service.placement_identity import parse_placement_id, placement_id_from_description
from app.service.placement_report import read_cycle_placement_report


def _money(value):
    return int(re.sub(r'\D', '', str(value)))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write('\n')
    path.chmod(0o600)


def validate_owner_confirmation(report, snapshot, placement_id):
    placement_id = parse_placement_id(placement_id).value
    if not snapshot.get('operator_authorization'):
        raise ValueError('owner authorization is required')
    marketing = snapshot['marketing_row']['values'][0]
    # Exact native range F:X: status F, model I, equipment J, price L,
    # year M and Avito URL X. VIN K is intentionally not used.
    price, year, url = _money(marketing[6]), int(marketing[7]), marketing[18]
    key = canonical_listing_key(EngineType.AVITO, url)
    if marketing[0] != 'Актуально' or not key:
        raise ValueError('marketing must contain an active row and an Avito URL')
    price_matches = [
        r
        for r in snapshot['marketing_prices']['values']
        if r and re.search(r'\d', str(r[0])) and _money(r[0]) == price
    ]
    if len(price_matches) != 1:
        raise ValueError('marketing price is not unique; independent owner review is needed')
    rows_by_sheet = {}
    for name, field in [
        ('avito-feed-new', 'new_feed_identities'),
        ('avito-feed-used', 'used_feed_identities'),
    ]:
        rows_by_sheet[name] = [
            {'Id': row[0], 'AvitoId': row[2] if len(row) > 2 else ''}
            for row in snapshot[field]['values']
            if row and row[0]
        ]
    rows = [row for values in rows_by_sheet.values() for row in values]
    matched = [row for row in rows if row['Id'] == placement_id]
    if (
        len(matched) != 1
        or f'avito:{matched[0]["AvitoId"]}' != key
        or sum(row['AvitoId'] == matched[0]['AvitoId'] for row in rows) != 1
    ):
        raise ValueError('feed ID and platform URL must match one unique feed row')
    feed_description, feed_price = snapshot['feed_target_description_price']['values'][0]
    if (
        placement_id_from_description(
            BeautifulSoup(feed_description, 'html.parser').get_text(' ', strip=True)
        )
        != placement_id
        or _money(feed_price) != price
    ):
        raise ValueError('labelled feed ID or feed price disagrees with marketing')
    old_ids = {}
    for finding in report['findings']:
        value = finding.get('suggested_placement_id') or finding.get('placement_id')
        if value and finding.get('feed_row') and finding.get('feed_sheet') in rows_by_sheet:
            try:
                old_ids[finding['feed_sheet'], finding['feed_row']] = parse_placement_id(value).value
            except ValueError:
                pass
    current_ids = {
        (sheet, number): row['Id']
        for sheet, values in rows_by_sheet.items()
        for number, row in enumerate(values, 4)
    }
    if current_ids != old_ids:
        raise ValueError('feed identity rows changed since the saved cycle')
    matches = [
        c
        for c in report['observed_cards']
        if c['source'] == 'avito' and canonical_listing_key(EngineType.AVITO, c['url']) == key
    ]
    if len(matches) != 1:
        raise ValueError('one saved card is required')
    card = matches[0]
    if (
        card['state'] != 'active'
        or card['card'].get('price') != price
        or card['card'].get('year') != year
        or card['card'].get('placement_id_raw')
    ):
        raise ValueError('saved card must agree with marketing and have no conflicting raw ID claim')
    if sum(c.get('card', {}).get('price') == price for c in report['observed_cards']) != 1:
        raise ValueError('the saved catalogue price is ambiguous')
    feed_model = snapshot['feed_target_model_year']['values'][0]
    if (
        not all(
            'mercedes' in str(value).lower() for value in (marketing[3], feed_model[0], card['card']['title'])
        )
        or int(feed_model[8]) != year
        or not re.search(r'\bV[-\s]', card['card']['title'], re.I)
    ):
        raise ValueError('model or year disagrees between marketing, feed and card')
    visual = str.maketrans(
        {
            'А': 'A',
            'В': 'B',
            'Е': 'E',
            'К': 'K',
            'М': 'M',
            'Н': 'H',
            'О': 'O',
            'Р': 'P',
            'С': 'C',
            'Т': 'T',
            'У': 'Y',
            'Х': 'X',
        }
    )
    if 'GUARD' in str(marketing[4]).upper() and 'GUARD' not in str(
        card['card'].get('description_excerpt', '')
    ).upper().translate(visual):
        raise ValueError('special GUARD equipment is not corroborated by the saved card')
    if not report.get('catalogue_enumeration_complete', {}).get('avito'):
        raise ValueError('seller inventory is incomplete')
    return {
        'placement_id': placement_id,
        'listing_key': key,
        'url': url,
        'price': price,
        'year': year,
        'marketing_model': marketing[3],
        'equipment': marketing[4],
        'card': card,
        'rows_by_sheet': rows_by_sheet,
    }


def confirmed_operator_id(db, evidence_dir, url, inspection):
    """Reuse an explicit saved confirmation, never a price-only heuristic."""
    key = canonical_listing_key(EngineType.AVITO, url)
    if not key:
        return None
    case_path = Path(evidence_dir) / 'identity_confirmations' / f'{key.replace(":", "_")}.json'
    if not case_path.is_file() or case_path.is_symlink():
        return None
    try:
        case = json.loads(case_path.read_text())
        value = parse_placement_id(case['placement_id']).value
        card = inspection.get('card') or {}
        if (
            case.get('schema_version') != 1
            or case.get('listing_key') != key
            or inspection.get('state') != 'active'
            or card.get('placement_id_raw')
            or card.get('price') != case['price']
            or card.get('year') != case['year']
        ):
            return None
        identity = (
            db.query(ListingPlacementIdentity)
            .filter_by(
                source=EngineType.AVITO,
                placement_id=value,
                listing_id=case['listing_id'],
            )
            .one_or_none()
        )
        listing = db.get(Listing, case['listing_id'])
        if (
            identity is None
            or listing is None
            or canonical_listing_key(EngineType.AVITO, listing.source_avito) != key
        ):
            return None
        return value
    except (KeyError, ValueError, TypeError, OSError):
        return None


def complete_saved_identity_review(db, *, cycle_id, placement_id, evidence_dir):
    root = Path(evidence_dir).resolve()
    cycle = db.get(MonitoringCycle, cycle_id)
    if cycle is None or cycle.status != 'partial' or cycle.finished_at is None:
        raise ValueError('a finished partial source cycle is required')
    if set(cycle.summary.get('partial_reasons', [])) - {
        'placement_reconciliation_incomplete',
        'pending_checks',
    }:
        raise ValueError('unrelated partial reasons must not be cleared')
    report = read_cycle_placement_report(cycle, str(root))
    review_dir = root / 'cycles' / cycle_id / 'identity_review'
    source_path = review_dir / 'source_snapshot.json'
    source = json.loads(source_path.read_text())
    proof = validate_owner_confirmation(report, source, placement_id)
    listing_matches = [
        item
        for item in db.query(Listing).all()
        if canonical_listing_key(EngineType.AVITO, item.source_avito) == proof['listing_key']
    ]
    if len(listing_matches) != 1:
        raise ValueError('one marketing registry listing is required')
    listing = listing_matches[0]
    for card in report['observed_cards']:
        image, manifest_path = root / card['evidence'], root / card['evidence_manifest']
        meta = json.loads(manifest_path.read_text())
        if (
            card['state'] != 'active'
            or meta.get('http_status') != 200
            or meta.get('screenshot_sha256') != _sha(image)
        ):
            raise ValueError('all direct-card evidence must remain intact')
    pending_path = root / cycle.summary['pending_checks']['report_path']
    pending = json.loads(pending_path.read_text())
    if pending['items'] != [
        {
            'phase': 'identity_coverage',
            'reason': 'incomplete',
            'report_path': f'cycles/{cycle_id}/placement_reconciliation.json',
            'source': 'avito',
        }
    ]:
        raise ValueError('unrelated unfinished checks must not be cleared')
    if report.get('unverified_cards_by_source') != {'avito': 1}:
        raise ValueError('the review only resolves one missing ID')
    if report.get('catalogue_enumeration_complete') != {'avito': True}:
        raise ValueError('only a complete Avito-only cycle can be finalized by this review')
    existing = db.query(ListingPlacementIdentity).filter_by(source=EngineType.AVITO).all()
    if any(x.placement_id == placement_id or x.listing_id == listing.id for x in existing):
        raise ValueError('identity already bound; review the existing association')
    now = datetime.now(UTC).isoformat()
    original_path = root / cycle.summary['placement_reconciliation']['report_path']
    confirmation = {k: v for k, v in proof.items() if k not in {'card', 'rows_by_sheet'}}
    confirmation.update(
        schema_version=1,
        cycle_id=cycle_id,
        listing_id=listing.id,
        confirmed_at_utc=now,
        actor='owner_authorization_in_chat',
        source_snapshot_sha256=_sha(source_path),
        source_snapshot_path=source_path.relative_to(root).as_posix(),
        original_report_sha256=_sha(original_path),
        evidence=proof['card']['evidence'],
        evidence_manifest=proof['card']['evidence_manifest'],
    )
    case_relative = f'identity_confirmations/{proof["listing_key"].replace(":", "_")}.json'
    identity = ListingPlacementIdentity(
        source=EngineType.AVITO, placement_id=placement_id, listing_id=listing.id, evidence=case_relative
    )
    db.add(identity)
    db.flush()
    current_urls = {x.placement_id: db.get(Listing, x.listing_id).source_avito for x in [*existing, identity]}
    opened = [
        OpenedMarketplaceCard(
            c['url'],
            {
                'state': c['state'],
                'card': c['card'],
                'evidence': c['evidence'],
                'evidence_manifest': c['evidence_manifest'],
            },
        )
        for c in report['observed_cards']
    ]
    findings = reconcile_avito_placements(
        proof['rows_by_sheet'],
        opened,
        first_data_rows={'avito-feed-new': 4, 'avito-feed-used': 4},
        current_urls_by_placement_id=current_urls,
        catalogue_complete=True,
        operator_confirmed_ids={proof['listing_key']: placement_id},
    )
    reviewed = deepcopy(report)
    reviewed['findings'] = [asdict(f) for f in findings]
    reviewed['catalogue_complete']['avito'] = True
    reviewed['unverified_cards_by_source']['avito'] = 0
    reviewed['identity_review'] = confirmation
    reviewed['original_report_path'] = original_path.relative_to(root).as_posix()
    reviewed['created_at_utc'] = now
    target = next(
        c
        for c in reviewed['observed_cards']
        if canonical_listing_key(EngineType.AVITO, c['url']) == proof['listing_key']
    )
    target['identity_confirmation'] = confirmation
    # The raw parsed ID stays null: this is a reviewed association, not an ID
    # supposedly observed in the advertisement.
    report_relative = f'cycles/{cycle_id}/placement_reconciliation_reviewed.json'
    pending_relative = f'cycles/{cycle_id}/pending_checks_reviewed.json'
    _write_once(root / case_relative, confirmation)
    _write_once(root / report_relative, reviewed)
    _write_once(
        root / pending_relative,
        {**pending, 'count': 0, 'items': [], 'created_at_utc': now, 'identity_review': confirmation},
    )
    summary = deepcopy(cycle.summary)
    summary['partial_reasons'] = [
        r
        for r in summary['partial_reasons']
        if r not in {'placement_reconciliation_incomplete', 'pending_checks'}
    ]
    if summary['partial_reasons']:
        raise ValueError('unrelated partial reasons must not be cleared')
    summary['placement_reconciliation'].update(
        status='complete',
        report_path=report_relative,
        findings=len(findings),
        codes=dict(Counter(f.code for f in findings)),
    )
    summary['pending_checks'] = {'count': 0, 'by_phase': {}, 'report_path': pending_relative}
    summary['status'] = cycle.status = 'completed'
    summary['post_run_identity_review'] = confirmation
    cycle.summary = summary
    return {
        'cycle_id': cycle_id,
        'status': 'completed',
        'report_path': report_relative,
        'pending_checks': 0,
        'placement_id': placement_id,
        'listing_key': proof['listing_key'],
    }
