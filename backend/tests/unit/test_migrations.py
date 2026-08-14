from alembic.config import Config

from streetpoker.db.migrations import escape_alembic_config_value


def test_percent_encoded_database_url_can_be_set_on_alembic_config() -> None:
    database_url = "postgresql+psycopg://streetpoker:password%40example@127.0.0.1:5432/streetpoker"
    alembic_config = Config()

    alembic_config.set_main_option(
        "sqlalchemy.url",
        escape_alembic_config_value(database_url),
    )

    assert alembic_config.get_main_option("sqlalchemy.url") == database_url
