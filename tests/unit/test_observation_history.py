import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    EngineType,
    Listing,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
    ScanRun,
    SearchFilter,
)
from app.service.report import (
    listing_catalog_context,
    listing_detail_context,
    observation_history_context,
)


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "history.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _seed(session):
    listing = Listing(
        vehicle_signature='VIN-HISTORY-1',
        vin='VIN-HISTORY-1',
        brand='<script>Unsafe Brand</script>',
        model='V-Class',
        generation='V-VIP',
        price_hint=44_990_000,
        source_auto_ru='https://auto.ru/cars/used/sale/brand/model/1234567890-test/',
        is_active=True,
    )
    auto_filter = SearchFilter(
        id='filter-auto',
        source=EngineType.AUTO_RU,
        external_key='auto-history',
        name='Auto history',
        raw_url='https://auto.ru/moskva/cars/used/',
    )
    avito_filter = SearchFilter(
        id='filter-avito',
        source=EngineType.AVITO,
        external_key='avito-history',
        name='Avito history',
        raw_url='https://www.avito.ru/moskva/avtomobili',
    )
    auto_run = ScanRun(id='run-auto', source=EngineType.AUTO_RU, network_profile='cloud_no_vpn')
    avito_run = ScanRun(id='run-avito', source=EngineType.AVITO, network_profile='cloud_no_vpn')
    session.add_all([listing, auto_filter, avito_filter, auto_run, avito_run])
    session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            ListingObservation(
                id='observation-auto',
                run_id=auto_run.id,
                listing_id=listing.id,
                filter_id=auto_filter.id,
                source=EngineType.AUTO_RU,
                page_number=2,
                position_in_page=3,
                absolute_position=41,
                found=True,
                state=ObservationState.FOUND,
                title='<img src=x onerror=alert(1)>',
                listing_url='https://auto.ru/cars/used/sale/brand/model/1234567890-test/',
                observed_at=now,
                raw_payload={'scan_diagnostics': {'page_2_evidence': '/app/artifacts/proof.png'}},
            ),
            ListingObservation(
                id='observation-avito',
                run_id=avito_run.id,
                listing_id=listing.id,
                filter_id=avito_filter.id,
                source=EngineType.AVITO,
                page_number=0,
                position_in_page=0,
                found=False,
                state=ObservationState.ABSENT_UNCERTAIN,
                observed_at=now - timedelta(hours=1),
            ),
        ]
    )
    session.commit()
    return listing


def test_history_filters_and_exposes_absolute_position_and_evidence(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        context = observation_history_context(
            session,
            source=EngineType.AUTO_RU,
            brand=listing.brand,
            model='V-Class',
            state=ObservationState.FOUND,
        )

        assert context['total'] == 1
        assert context['rows'][0]['observation'].absolute_position == 41
        assert context['rows'][0]['evidence_pages'] == [2]
        assert context['rows'][0]['evidence_kind'] == 'page'
        assert observation_history_context(session, source=EngineType.AVITO)['total'] == 1
        assert listing_catalog_context(session, platform=EngineType.AUTO_RU)['total'] == 1
        assert listing_catalog_context(session, platform=EngineType.AVITO)['total'] == 0
        listing.source_avito = 'https://www.avito.ru/moskva/avtomobili/test_9876543210'
        session.commit()
        assert listing_catalog_context(session, platform=EngineType.AVITO)['total'] == 1
    finally:
        session.close()
        engine.dispose()


def test_history_and_listing_templates_escape_imported_content(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        template_dir = Path(__file__).parents[2] / 'src' / 'app' / 'templates'
        environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html']),
        )
        history_html = environment.get_template('history.html').render(
            context=observation_history_context(session), auth_enabled=False
        )
        detail = listing_detail_context(session, listing.id)
        detail_html = environment.get_template('listing_detail.html').render(
            context=detail, auth_enabled=False
        )
        catalog_html = environment.get_template('listing_catalog.html').render(
            context=listing_catalog_context(session), auth_enabled=False
        )

        assert '&lt;script&gt;Unsafe Brand&lt;/script&gt;' in history_html
        assert '&lt;img src=x onerror=alert(1)&gt;' in history_html
        assert '<img src=x onerror=alert(1)>' not in history_html
        assert '&lt;script&gt;Unsafe Brand&lt;/script&gt;' in detail_html
        assert '&lt;script&gt;Unsafe Brand&lt;/script&gt;' in catalog_html
        assert 'Auto.ru · стр. 2' in catalog_html
        assert 'Avito — нет' in catalog_html
        assert '44 990 000 ₽' in catalog_html
        assert 'V-VIP' in catalog_html
        assert 'стр. 2' in catalog_html
        assert 'Карточка объявления появится после успешной проверки' in catalog_html
        assert listing_catalog_context(session)['cards'][0]['observation'] is None
        assert detail['previews'] == []
        assert detail['page_statistics']['overall']['found_total'] == 1
        assert detail['page_statistics']['overall']['page_counts'] == {1: 0, 2: 1, 3: 0}
        assert detail['page_statistics']['overall']['page_shares'][2] == 100.0
        assert detail['page_statistics']['by_filter'][0]['filter_name'] == 'Auto history'
        assert 'Статистика страниц за всю историю' in detail_html
    finally:
        session.close()
        engine.dispose()


def test_listing_page_statistics_keep_history_per_filter(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        now = datetime.now(UTC)
        session.add_all(
            [
                ListingObservation(
                    run_id='run-auto',
                    listing_id=listing.id,
                    filter_id='filter-auto',
                    source=EngineType.AUTO_RU,
                    page_number=1,
                    position_in_page=4,
                    absolute_position=4,
                    found=True,
                    state=ObservationState.FOUND,
                    observed_at=now + timedelta(seconds=1),
                ),
                ListingObservation(
                    run_id='run-auto',
                    listing_id=listing.id,
                    filter_id='filter-auto',
                    source=EngineType.AUTO_RU,
                    page_number=3,
                    position_in_page=5,
                    absolute_position=65,
                    found=True,
                    state=ObservationState.FOUND,
                    observed_at=now + timedelta(seconds=2),
                ),
            ]
        )
        session.commit()

        statistics = listing_detail_context(session, listing.id)['page_statistics']

        assert statistics['overall']['found_total'] == 3
        assert statistics['overall']['page_counts'] == {1: 1, 2: 1, 3: 1}
        assert statistics['overall']['page_shares'] == {1: 33.3, 2: 33.3, 3: 33.3}
        assert statistics['overall']['latest'].page_number == 3
        assert statistics['by_filter'][0]['page_counts'] == {1: 1, 2: 1, 3: 1}
    finally:
        session.close()
        engine.dispose()


def test_sales_catalog_uses_head_table_and_direct_card_proof(tmp_path, monkeypatch):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        listing.source_avito = 'https://www.avito.ru/moskva/avtomobili/test_9876543210'
        session.add(
            ListingReconciliation(
                id='direct-avito',
                batch_id='batch-direct',
                listing_id=listing.id,
                source=EngineType.AVITO,
                state='verified',
                url=listing.source_avito,
                reason='Есть в каталоге',
                details={
                    'direct_inspection': {
                        'state': 'removed',
                        'status_code': 'closed',
                        'reason': 'На странице объявления: «Объявление закрыто»',
                        'evidence': 'direct.png',
                        'card': {'vat_status': 'С НДС', 'availability': 'В пути'},
                    }
                },
                checked_at=datetime.now(UTC),
            )
        )
        session.commit()
        audit_dir = tmp_path / 'head'
        audit_dir.mkdir()
        (audit_dir / 'latest.json').write_text(
            json.dumps({
                'records': [
                    {
                        'row_number': 2,
                        'vin': listing.vin,
                        'brand_model': 'Mercedes-Benz V-Class',
                        'configuration': 'V-VIP',
                        'price': 46_990_000,
                        'year': 2026,
                        'vat': 'с НДС',
                        'availability': 'В пути',
                        'status': 'Актуально',
                        'active': True,
                        'auto_ru_url': listing.source_auto_ru,
                        'avito_url': listing.source_avito,
                    },
                    {
                        'row_number': 3,
                        'vin': None,
                        'brand_model': 'Maestra V800',
                        'configuration': 'Business',
                        'price': 18_500_000,
                        'year': 2026,
                        'vat': 'Без НДС',
                        'availability': 'В пути',
                        'status': 'Актуально',
                        'active': True,
                        'auto_ru_url': None,
                        'avito_url': None,
                    },
                ]
            }),
            encoding='utf-8',
        )
        monkeypatch.setattr('app.service.report.settings.head_table_audit_dir', str(audit_dir))

        context = listing_catalog_context(session)
        template_dir = Path(__file__).parents[2] / 'src' / 'app' / 'templates'
        environment = Environment(
            loader=FileSystemLoader(template_dir), autoescape=select_autoescape(['html'])
        )
        html = environment.get_template('listing_catalog.html').render(
            context=context, auth_enabled=False
        )

        assert context['expected_total'] == 2
        assert context['unmatched_total'] == 1
        assert context['cards'][0]['preview_kind'] == 'Прямая карточка'
        assert '46 990 000 ₽' in html
        assert 'с НДС' in html
        assert 'Объявление закрыто' in html
        assert 'Скриншот прямой карточки' in html
        assert 'Maestra V800' in html
        assert 'Не подключены к мониторингу · 1' in html
    finally:
        session.close()
        engine.dispose()


def test_catalog_shows_reviewable_unique_id_without_replacing_old_link(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        placement_id = 'MBVC011220252508260001'
        session.add(ListingReconciliation(
            id='id-review', batch_id='batch-id', listing_id=listing.id,
            source=EngineType.AUTO_RU, state='removed', url=listing.source_auto_ru,
            reason='Старая карточка продана', checked_at=datetime.now(UTC),
            details={'network_profile': 'local_browser',
                     'direct_inspection': {'state': 'removed', 'status_code': 'sold'}},
            candidates=[{
                'placement_id': placement_id, 'id_match_basis': 'exact',
                'url': 'https://auto.ru/cars/new/group/mercedes/v_klasse/1133841448-test/',
                'evidence': 'card.png',
            }],
        ))
        session.commit()

        catalog = listing_catalog_context(session)
        detail = listing_detail_context(session, listing.id)
        environment = Environment(
            loader=FileSystemLoader(Path(__file__).parents[2] / 'src' / 'app' / 'templates'),
            autoescape=select_autoescape(['html']),
        )
        catalog_html = environment.get_template('listing_catalog.html').render(
            context=catalog, auth_enabled=False,
        )
        detail_html = environment.get_template('listing_detail.html').render(
            context=detail, auth_enabled=False,
        )

        assert catalog['cards'][0]['platforms']['auto_ru']['placement_id'] == {
            'value': placement_id, 'basis': 'review',
        }
        assert placement_id in catalog_html
        assert 'Кандидат перевыкладки — подтвердить ссылку' in catalog_html
        assert placement_id in detail_html
        assert listing.source_auto_ru == 'https://auto.ru/cars/used/sale/brand/model/1234567890-test/'
    finally:
        session.close()
        engine.dispose()


def test_catalog_hides_untrusted_or_conflicting_unique_ids(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        record = ListingReconciliation(
            id='id-conflict', batch_id='batch-id', listing_id=listing.id,
            source=EngineType.AUTO_RU, state='verified', url=listing.source_auto_ru,
            reason='Есть в каталоге', checked_at=datetime.now(UTC),
            details={'network_profile': 'local_browser', 'direct_inspection': {
                'state': 'active', 'card': {'placement_id': 'MBVC011220252508260001'},
            }},
            candidates=[{
                'placement_id': 'MBVC011220252508260007', 'id_match_basis': 'exact',
                'url': 'https://auto.ru/cars/new/group/mercedes/v_klasse/1133976818-test/',
                'evidence': 'other.png',
            }],
        )
        session.add(record)
        session.commit()
        assert listing_catalog_context(session)['cards'][0]['platforms']['auto_ru']['placement_id'] is None

        record.candidates = []
        session.commit()
        assert listing_catalog_context(session)['cards'][0]['platforms']['auto_ru']['placement_id'] == {
            'value': 'MBVC011220252508260001', 'basis': 'card',
        }

        record.details = {'network_profile': 'unknown', 'direct_inspection': {
            'state': 'active', 'card': {'placement_id': 'MBVC011220252508260001'},
        }}
        session.commit()
        assert listing_catalog_context(session)['cards'][0]['platforms']['auto_ru']['placement_id'] is None
    finally:
        session.close()
        engine.dispose()


def test_catalog_buttons_show_latest_page_for_each_marketplace(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = _seed(session)
        listing.source_avito = 'https://www.avito.ru/moskva/avtomobili/test_9876543210'
        now = datetime.now(UTC)
        session.add(
            ListingObservation(
                run_id='run-avito',
                listing_id=listing.id,
                filter_id='filter-avito',
                source=EngineType.AVITO,
                page_number=3,
                position_in_page=5,
                absolute_position=65,
                found=True,
                state=ObservationState.FOUND,
                listing_url=listing.source_avito,
                observed_at=now + timedelta(seconds=1),
                raw_payload={'card_evidence': 'avito_card_9876543210.png'},
            )
        )
        session.commit()
        template_dir = Path(__file__).parents[2] / 'src' / 'app' / 'templates'
        environment = Environment(
            loader=FileSystemLoader(template_dir), autoescape=select_autoescape(['html'])
        )
        html = environment.get_template('listing_catalog.html').render(
            context=listing_catalog_context(session), auth_enabled=False
        )
        assert 'Auto.ru · стр. 2' in html
        assert 'Avito · стр. 3' in html
        assert 'Auto.ru ↗' not in html
        assert 'Avito ↗' not in html
        context = listing_catalog_context(session)
        assert context['cards'][0]['observation'].source == EngineType.AVITO
        assert listing_detail_context(session, listing.id)['previews'][0][
            'observation'
        ].source == EngineType.AVITO
    finally:
        session.close()
        engine.dispose()
