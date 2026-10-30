"""Immutable checkpoints for complete browser observations, not a second scan."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.scraper.base import ListingHit, ScanResult
from app.scraper.browser_session import run_browser_task
from app.service.artifact_bundle import publish_bundle, verify_bundle
from app.service.evidence import EvidenceAccessError, read_direct_card_evidence, read_evidence_manifest

_active: ContextVar = ContextVar('cycle_recovery', default=None)
MAX_RECOVERY_AGE = timedelta(hours=6)


class RecoveryError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def recovery_config():
    scraper = Path(__file__).resolve().parents[1] / 'scraper'
    return _digest({
        'format': 1, 'app_version': settings.app_version,
        'network_profile': settings.network_profile, 'sources': sorted(settings.scan_engines),
        'pages': settings.scan_pages_limit,
        'source': settings.source_google_sheet_export_url or settings.source_csv_path,
        'workbook': settings.placement_feed_workbook_url or settings.head_table_google_sheet_export_url,
        'scraper_code': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(scraper.glob('*.py'))},
    })


class RecoveryJournal:
    def __init__(self, root, cycle_id, *, parent_id=None, config=None, now=None, progress=None):
        self.root = Path(root).resolve()
        self.cycle_id = cycle_id
        self.parent_id = parent_id
        self.config = config or recovery_config()
        self.clock = now or (lambda: datetime.now(UTC))
        self.progress = progress or (lambda _: None)
        self.counts = Counter()
        self.catalogues_refreshed = False
        for identity in (cycle_id, parent_id):
            if identity is not None and not re.fullmatch(r'[A-Za-z0-9-]{1,64}', identity):
                raise RecoveryError('invalid recovery cycle ID')
        if parent_id == cycle_id:
            raise RecoveryError('a recovery attempt needs a separate cycle ID')
        if parent_id:
            meta = self._read(parent_id, 'manifest')
            if meta.get('config_sha256') != self.config:
                raise RecoveryError('recovery settings or scraper code differ; start a fresh cycle')
            self._recent(meta.get('created_at'))
        self._write('manifest', {'schema_version': 1, 'cycle_id': cycle_id,
                                'parent_id': parent_id, 'config_sha256': self.config,
                                'created_at': self.clock().isoformat()})

    def _directory(self, cycle_id, key):
        candidate = self.root / 'cycles' / cycle_id / 'recovery' / key
        current = candidate
        while current != self.root:
            if current.is_symlink():
                raise RecoveryError('symlinks are not accepted for recovery')
            current = current.parent
        return candidate

    def _read(self, cycle_id, key):
        directory = self._directory(cycle_id, key)
        try:
            verify_bundle(directory, 'ready.json', required=('record.json',))
            return json.loads((directory / 'record.json').read_text())
        except (OSError, ValueError) as exc:
            raise RecoveryError('recovery checkpoint is missing or damaged') from exc

    def _write(self, key, payload):
        publish_bundle(self._directory(self.cycle_id, key), {'record.json': _json(payload) + '\n'},
                       marker='ready.json', commit_member='record.json')

    def _recent(self, value):
        try:
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is None or not timedelta(0) <= self.clock() - stamp <= MAX_RECOVERY_AGE:
                raise ValueError('expired or future checkpoint')
            return stamp
        except (TypeError, ValueError) as exc:
            raise RecoveryError('checkpoint is expired or has an invalid timestamp') from exc

    def _load(self, kind, scope):
        key = _digest({'kind': kind, 'scope': scope})
        for cycle_id in (self.cycle_id, self.parent_id):
            if not cycle_id:
                continue
            try:
                saved = self._read(cycle_id, key)
                if (saved['kind'] != kind or saved['scope'] != scope
                        or saved['config_sha256'] != self.config):
                    raise RecoveryError('checkpoint scope differs')
                self._recent(saved['captured_at'])
                return key, saved
            except (RecoveryError, KeyError):
                continue
        return key, None

    def _save(self, key, kind, scope, value, captured_at):
        if (self._directory(self.cycle_id, key) / 'ready.json').exists():
            # A later visit may be necessary after expiry. Never overwrite its
            # earlier immutable observation in the same attempt.
            return
        self._write(key, {'schema_version': 1, 'source_cycle_id': self.cycle_id,
                         'config_sha256': self.config, 'kind': kind, 'scope': scope,
                         'captured_at': captured_at, 'value': value})

    def _reused(self, kind, saved):
        self._write(_digest({'kind': kind, 'scope': saved['scope']}), saved)
        self.counts[kind] += 1
        provenance = {'source_cycle_id': saved['source_cycle_id'], 'captured_at': saved['captured_at'],
                      'reused_in_cycle_id': self.cycle_id, 'fresh_browser_visit': False}
        self.progress({'event': 'recovery_checkpoint_reused', 'kind': kind, **provenance})
        return provenance

    def search(self, scope, factory):
        key, saved = self._load('search', scope)
        if saved:
            try:
                value = copy.deepcopy(saved['value'])
                result = ScanResult(**{**value, 'scanned_at': datetime.fromisoformat(value['scanned_at']),
                                       'hits': [ListingHit(**hit) for hit in value['hits']]})
                self._validate_search(result, scope)
                result.diagnostics['recovery'] = self._reused('search', saved)
                return result
            except (RecoveryError, KeyError, TypeError, ValueError):
                pass
        result = factory()
        try:
            self._validate_search(result, scope)
        except RecoveryError:
            return result  # Incomplete observations stay incomplete; never reusable success.
        self._save(key, 'search', scope, {**asdict(result), 'scanned_at': result.scanned_at.isoformat()},
                   result.scanned_at.isoformat())
        return result

    def _validate_search(self, result, scope):
        source, _filter_id, search_url, _version, limit = scope
        if (result.complete is not True or result.error or result.page_count < 1
                or result.requested_pages != limit
                or result.page_count > limit or result.page_count < limit and not result.exhausted):
            raise RecoveryError('search completion is not proven')
        if source == 'auto_ru':
            from app.scraper.auto_ru import _list_url, _page_url
            base = _list_url(search_url)
        elif source == 'avito':
            from app.scraper.avito import _page_url
            base = search_url
        else:
            raise RecoveryError('invalid marketplace')
        self._recent(result.scanned_at.isoformat())
        for page in range(1, result.page_count + 1):
            try:
                manifest = read_evidence_manifest(result.diagnostics.get(f'page_{page}_evidence'), str(self.root))
            except EvidenceAccessError as exc:
                raise RecoveryError('search evidence is not intact') from exc
            url = result.diagnostics.get(f'page_{page}_requested_url')
            final_url = result.diagnostics.get(f'page_{page}_final_url')
            if (url != _page_url(base, page) or manifest.get('source') != source
                    or manifest.get('purpose') != 'search_page' or manifest.get('page_number') != page
                    or manifest.get('http_status') != 200
                    # Search evidence binds the original filter URL plus page
                    # number; the browser request includes generated page/list parameters.
                    or manifest.get('requested_url_sha256') != hashlib.sha256(search_url.encode()).hexdigest()
                    or not isinstance(final_url, str)
                    or manifest.get('final_url_sha256') != hashlib.sha256(final_url.encode()).hexdigest()):
                raise RecoveryError('search evidence scope differs')
            self._recent(manifest.get('captured_at'))

    def card(self, source, url, factory, *, price_hint=None):
        scope = [source.value, url]
        key, saved = self._load('card', scope)
        if saved:
            try:
                value = copy.deepcopy(saved['value'])
                self._validate_card(source.value, url, value)
                if price_hint is not None and float((value.get('card') or {}).get('price', -1)) != float(price_hint):
                    raise RecoveryError('fresh catalogue price differs from checkpoint')
                value['recovery'] = self._reused('card', saved)
                return value
            except (RecoveryError, KeyError, TypeError, ValueError):
                pass
        value = factory()
        try:
            manifest = self._validate_card(source.value, url, value)
        except RecoveryError:
            return value
        self._save(key, 'card', scope, value, manifest['captured_at'])
        return value

    def _validate_card(self, source, url, value):
        if value.get('state') != 'active':
            raise RecoveryError('card is not verified')
        try:
            manifest = read_direct_card_evidence({**value, 'source': source, 'url': url}, str(self.root))
            self._recent(manifest['captured_at'])
        except (EvidenceAccessError, KeyError) as exc:
            raise RecoveryError('card evidence is not verified') from exc
        if value['state'] == 'active':
            card = value.get('card') or {}
            capture = card.get('description_capture') or {}
            description = card.get('description')
            if (not isinstance(description, str) or not description
                    or capture.get('software_truncated') is not False
                    or capture.get('sha256') != hashlib.sha256(description.encode()).hexdigest()):
                raise RecoveryError('full card description is not verified')
        return manifest

    def summary(self):
        return {'resume_of_cycle_id': self.parent_id, 'reused_search_filters': self.counts['search'],
                'reused_cards': self.counts['card'], 'max_age_seconds': int(MAX_RECOVERY_AGE.total_seconds()),
                'catalogues_refreshed': self.catalogues_refreshed, 'original_timestamps_preserved': True}


@contextmanager
def recovery_context(journal):
    token = _active.set(journal)
    try:
        yield
    finally:
        _active.reset(token)


def recovery_search(scope, factory):
    journal = _active.get()
    return journal.search(list(scope), factory) if journal else factory()


def inspect_with_recovery(inspector, source, url, progress, *, price_hint=None):
    journal = _active.get()
    def factory():
        return run_browser_task(inspector(source, url, progress))
    return journal.card(source, url, factory, price_hint=price_hint) if journal else factory()


def recovery_summary():
    journal = _active.get()
    return journal.summary() if journal else None


def mark_recovery_catalogues_refreshed():
    journal = _active.get()
    if journal:
        journal.catalogues_refreshed = True
