from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.models import Base, ListingObservation, ManagerFeedback, ObservationState
from app.service.analytics import analytics_context
from app.service.report import dashboard_context


def test_dashboard_observation_state_binds_postgresql_review_value():
    state_type = ListingObservation.__table__.c.state.type
    dialect = postgresql.dialect()
    bind = state_type.bind_processor(dialect)
    result = state_type.result_processor(dialect, None)

    assert state_type.enums == [
        'FOUND',
        'ABSENT_CONFIRMED',
        'ABSENT_UNCERTAIN',
        'FILTER_MISMATCH',
        'review_required',
        'TECHNICAL_ERROR',
    ]
    assert bind is not None
    assert result is not None
    assert bind(ObservationState.REVIEW_REQUIRED) == 'review_required'
    assert result('review_required') == ObservationState.REVIEW_REQUIRED
    assert bind(ObservationState.FOUND) == 'FOUND'
    assert result('FOUND') == ObservationState.FOUND


def test_dashboard_template_renders_and_escapes_manager_input(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "dashboard.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            ManagerFeedback(
                message='<script>alert("xss")</script>',
                manager_name='Manager',
                severity='medium',
                category='other',
            )
        )
        session.commit()
        context = dashboard_context(session)
        context['workspace'] = analytics_context(session)
        template_dir = Path(__file__).parents[2] / 'src' / 'app' / 'templates'
        environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html']),
        )
        html = environment.get_template('dashboard.html').render(
            context=context, auth_enabled=False
        )

        assert 'A1 Search Monitor' in html
        assert 'Ход текущей проверки' in html
        assert '/static/progress.js' in html
        assert 'Мониторинг ещё не настроен' in html
        assert '&lt;script&gt;' in html
        assert '<script>alert("xss")</script>' not in html
    finally:
        session.close()
        engine.dispose()
