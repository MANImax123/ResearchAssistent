import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_database_url_uses_asyncpg() -> None:
    s = Settings(_env_file=None, postgres_host="db", postgres_user="u", postgres_password="p", postgres_db="d")

    assert s.database_url == "postgresql+asyncpg://u:p@db:5432/d"


def test_cors_origins_parsed_from_comma_list() -> None:
    s = Settings(_env_file=None, cors_origins="http://a.com, http://b.com,")

    assert s.cors_origin_list == ["http://a.com", "http://b.com"]


def test_prod_requires_strong_secret_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prod", secret_key="short")

    Settings(_env_file=None, app_env="prod", secret_key="x" * 32)
