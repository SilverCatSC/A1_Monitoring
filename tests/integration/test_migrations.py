from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def _upgrade(database_url: str, revision: str = 'head') -> None:
    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', database_url)
    command.upgrade(config, revision)


def test_migration_builds_clean_database(tmp_path):
    database_url = f'sqlite:///{tmp_path / "clean.db"}'
    _upgrade(database_url)
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {'alembic_version', 'listings', 'listing_observations', 'manager_feedback'} <= tables
        with engine.connect() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (
                '20260909_0006'
            )
    finally:
        engine.dispose()


def test_migration_adopts_pre_alembic_schema_without_data_loss(tmp_path):
    database_url = f'sqlite:///{tmp_path / "legacy.db"}'
    _upgrade(database_url, '20260904_0001')
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO listings (id, vehicle_signature, is_active) "
                "VALUES ('legacy-listing', 'legacy-signature', 1)"
                )
            )
        connection.execute(text('DROP TABLE alembic_version'))
    engine.dispose()

    _upgrade(database_url)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM listings')).scalar_one() == 1
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (
                '20260909_0006'
            )
    finally:
        engine.dispose()
