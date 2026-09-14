import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from app.models import EngineType, ListingObservation
from app.scraper.base import ListingHit, capture_listing_card_evidence, capture_page_evidence
from app.service.evidence import (
    EvidenceAccessError,
    cleanup_evidence,
    evidence_pages,
    has_card_evidence,
    read_evidence_manifest,
    resolve_observation_evidence,
)


def test_evidence_cleanup_only_removes_expired_png_files(tmp_path):
    old_png = tmp_path / 'old.png'
    old_manifest = tmp_path / 'old.png.json'
    fresh_png = tmp_path / 'fresh.png'
    old_text = tmp_path / 'keep.txt'
    for path in (old_png, fresh_png, old_text):
        path.write_text('fixture')
    old_manifest.write_text('{}')
    old_timestamp = (datetime.now(UTC) - timedelta(days=100)).timestamp()
    os.utime(old_png, (old_timestamp, old_timestamp))
    os.utime(old_text, (old_timestamp, old_timestamp))

    assert cleanup_evidence(str(tmp_path), retention_days=90) == 1
    assert not old_png.exists()
    assert not old_manifest.exists()
    assert fresh_png.exists()
    assert old_text.exists()


def _observation(path: str) -> ListingObservation:
    return ListingObservation(
        raw_payload={
            'scan_diagnostics': {
                'page_1_evidence': path,
                'page_bad_evidence': 'ignored.png',
            }
        }
    )


def test_evidence_resolver_only_returns_png_inside_evidence_root(tmp_path):
    screenshot = tmp_path / 'scan.png'
    screenshot.write_bytes(b'png fixture')
    observation = _observation(str(screenshot))

    assert evidence_pages(observation) == [1]
    assert resolve_observation_evidence(observation, 1, str(tmp_path)) == screenshot

    outside = tmp_path.parent / 'outside.png'
    outside.write_bytes(b'outside')
    observation.raw_payload['scan_diagnostics']['page_1_evidence'] = str(outside)
    with pytest.raises(EvidenceAccessError, match='invalid evidence path'):
        resolve_observation_evidence(observation, 1, str(tmp_path))


def test_evidence_resolver_rejects_missing_files_and_symlinks(tmp_path):
    missing = _observation('missing.png')
    with pytest.raises(EvidenceAccessError, match='invalid evidence path'):
        resolve_observation_evidence(missing, 1, str(tmp_path))

    target = tmp_path / 'target.png'
    target.write_bytes(b'png fixture')
    link = tmp_path / 'link.png'
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows account cannot create symlinks')
        raise
    with pytest.raises(EvidenceAccessError, match='invalid evidence file'):
        resolve_observation_evidence(_observation('link.png'), 1, str(tmp_path))


def test_card_evidence_is_preferred_for_found_observation(tmp_path):
    card = tmp_path / 'card.png'
    page = tmp_path / 'page.png'
    card.write_bytes(b'card')
    page.write_bytes(b'page')
    observation = ListingObservation(
        found=True,
        page_number=2,
        raw_payload={
            'card_evidence': card.name,
            'scan_diagnostics': {'page_1_evidence': page.name},
        },
    )
    assert evidence_pages(observation) == [2]
    assert has_card_evidence(observation) is True
    assert resolve_observation_evidence(observation, 2, str(tmp_path)) == card


class _FakeCard:
    def __init__(self):
        self.first = self

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def scroll_into_view_if_needed(self, **_kwargs):
        return None

    async def screenshot(self, *, path, **_kwargs):
        with open(path, 'wb') as output:
            output.write(b'card image')


class _FakeAnchors:
    def __init__(self):
        self.card = _FakeCard()

    async def count(self):
        return 1

    def nth(self, _index):
        return self

    def locator(self, selector):
        assert selector.startswith('xpath=ancestor::*')
        return self.card


class _FakePage:
    def locator(self, selector):
        assert '1234567890' in selector
        return _FakeAnchors()


class _FakeViewportPage:
    url = 'https://auto.ru/moskva/cars/all/?page=1'

    async def screenshot(self, *, path, **_kwargs):
        with open(path, 'wb') as output:
            output.write(b'page image')


def test_exact_listing_card_screenshot_is_saved(tmp_path):
    hit = ListingHit(
        external_id='1234567890',
        title='Car',
        url='https://auto.ru/cars/used/sale/brand/model/1234567890-test/',
        page_number=2,
        position=3,
        price=None,
        raw={},
    )
    stored = asyncio.run(
        capture_listing_card_evidence(
            _FakePage(),
            source=EngineType.AUTO_RU,
            search_url='https://auto.ru/moskva/cars/all/',
            page_number=2,
            hit=hit,
            evidence_dir=str(tmp_path),
        )
    )
    assert stored is not None
    assert stored.startswith('auto_ru_card_1234567890_')
    assert (tmp_path / stored).read_bytes() == b'card image'


def test_page_evidence_has_a_privacy_bounded_integrity_manifest(tmp_path):
    stored = asyncio.run(
        capture_page_evidence(
            _FakeViewportPage(),
            source=EngineType.AUTO_RU,
            search_url='https://auto.ru/moskva/cars/all/?dealer=secret',
            page_number=1,
            evidence_dir=str(tmp_path),
            purpose='search_page',
            final_url=_FakeViewportPage.url,
            http_status=200,
        )
    )

    assert stored is not None
    manifest = read_evidence_manifest(stored, str(tmp_path))
    assert manifest['source'] == 'auto_ru'
    assert manifest['purpose'] == 'search_page'
    assert manifest['http_status'] == 200
    assert manifest['screenshot_file'] == stored
    assert 'secret' not in (tmp_path / f'{stored}.json').read_text(encoding='utf-8')

    (tmp_path / stored).write_bytes(b'X' * len(b'page image'))
    with pytest.raises(EvidenceAccessError, match='integrity'):
        read_evidence_manifest(stored, str(tmp_path))
