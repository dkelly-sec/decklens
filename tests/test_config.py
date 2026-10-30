import pytest

from decklens.config import ConfigError, load_settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no stray .env file gets loaded
    monkeypatch.delenv("CR_API_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_missing_token_is_an_error():
    with pytest.raises(ConfigError, match="CR_API_TOKEN"):
        load_settings(require_database=False)


def test_placeholder_token_is_an_error(monkeypatch):
    monkeypatch.setenv("CR_API_TOKEN", "replace-me")
    with pytest.raises(ConfigError):
        load_settings(require_database=False)


def test_secrets_hidden_from_repr(monkeypatch):
    monkeypatch.setenv("CR_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:dbpass@127.0.0.1/db")
    settings = load_settings()
    text = repr(settings)
    assert "super-secret-token" not in text
    assert "dbpass" not in text
