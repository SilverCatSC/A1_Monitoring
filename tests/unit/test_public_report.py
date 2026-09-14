import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, EngineType, Listing, ManagerFeedback, ScanRun, ScanRunStatus
from app.service.public_report import public_report_context
from scripts.export_public_report import (
    PublicationRejected,
    _clean_previous_export,
    _validate_html,
    load_allowlist,
)


def test_public_projection_and_template_do_not_leak_private_listing_or_feedback(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "public.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    try:
        session.add_all(
            [
                Listing(
                    id='private-listing',
                    vehicle_signature='private-signature',
                    vin='W1VVNLTZ5S4556796',
                    source_auto_ru='https://auto.ru/cars/used/sale/private/123/',
                    source_avito='https://www.avito.ru/moskva/avtomobili/private_123',
                    is_active=True,
                ),
                ManagerFeedback(
                    message='Confidential feedback text',
                    manager_name='Private manager',
                ),
                ScanRun(
                    source=EngineType.AUTO_RU,
                    network_profile='local_browser',
                    status=ScanRunStatus.SUCCESS,
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=1),
                    filters_total=2,
                    filters_ok=2,
                ),
            ]
        )
        session.commit()

        context = public_report_context(session, now=now)
        serialised = json.dumps(context, default=str)
        template_dir = Path(__file__).parents[2] / 'src' / 'app' / 'templates'
        html = Environment(
            loader=FileSystemLoader(template_dir), autoescape=select_autoescape(['html'])
        ).get_template('public_report.html').render(data=context)

        for private_value in (
            'W1VVNLTZ5S4556796',
            'private-listing',
            'https://auto.ru/cars/used/sale/private/123/',
            'Confidential feedback text',
            'Private manager',
        ):
            assert private_value not in serialised
            assert private_value not in html
        assert context['summary']['active_listings'] == 1
        assert context['privacy']['contains_vin'] is False
        assert 'Auto.ru' in html
    finally:
        session.close()
        engine.dispose()


def test_allowlist_requires_recent_named_aggregate_approval(tmp_path):
    path = tmp_path / 'allowlist.json'
    path.write_text(
        json.dumps(
            {
                'scope': 'a1-aggregate-public-report-v1',
                'approved_by': 'owner',
                'approved_at': '2026-09-14T10:00:00+00:00',
                'expires_at': '2026-09-20T10:00:00+00:00',
            }
        ),
        encoding='utf-8',
    )

    approval = load_allowlist(path, now=datetime(2026, 9, 14, 12, tzinfo=UTC))

    assert approval['approved_by'] == 'owner'
    path.write_text(
        json.dumps(
            {
                'scope': 'a1-aggregate-public-report-v1',
                'approved_by': 'owner',
                'approved_at': '2026-07-01T10:00:00+00:00',
                'expires_at': '2026-09-20T10:00:00+00:00',
            }
        ),
        encoding='utf-8',
    )
    with pytest.raises(PublicationRejected, match='older than 31 days'):
        load_allowlist(path, now=datetime(2026, 9, 14, 12, tzinfo=UTC))


def test_public_export_rejects_internal_references():
    with pytest.raises(PublicationRejected, match='forbidden'):
        _validate_html('<a href="/api/v1/dashboard/listings/private">private</a>')


def test_public_export_refuses_to_mix_with_an_unknown_old_file(tmp_path):
    (tmp_path / 'old-private-report.html').write_text('private', encoding='utf-8')

    with pytest.raises(PublicationRejected, match='unexpected files'):
        _clean_previous_export(tmp_path)
