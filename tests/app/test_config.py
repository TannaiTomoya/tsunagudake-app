from secrets import token_urlsafe

import pytest

from app import create_app
from app.config import DEVELOPMENT_KEY, ConfigError, load_config, validate_config


def public_settings(mode: str) -> dict[str, str]:
    return {
        "APP_ENV": mode,
        "BASE_URL": "https://demo.example.invalid",
        "TRUSTED_HOSTS": "demo.example.invalid",
        "DATABASE_URL": "mysql+pymysql://localhost/example",
        "SECRET_KEY": token_urlsafe(32),
        "DEMO_PASSWORD": token_urlsafe(16),
        "SESSION_COOKIE_SECURE": "1",
    }


@pytest.mark.parametrize("mode", ["demo", "production"])
@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("SECRET_KEY", ""),
        ("SECRET_KEY", DEVELOPMENT_KEY),
        ("DATABASE_URL", "sqlite:///local.sqlite"),
        ("BASE_URL", "http://demo.example.invalid"),
        ("TRUSTED_HOSTS", ""),
        ("SESSION_COOKIE_SECURE", "0"),
    ],
)
def test_t30_refuses_unsafe_startup(
    monkeypatch: pytest.MonkeyPatch, mode: str, name: str, invalid: str
) -> None:
    for key, value in (public_settings(mode) | {name: invalid}).items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError, match=name):
        create_app()


def test_t30_unset_mode_requires_production_settings() -> None:
    config = load_config({})
    assert config["APP_ENV"] == "production"
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        validate_config(config)


@pytest.mark.parametrize(
    ("mode", "name", "invalid"),
    [("demo", "DEMO_PASSWORD", "short"), ("production", "AI_DRIVER", "fake")],
)
def test_t30_mode_specific_rules(mode: str, name: str, invalid: str) -> None:
    settings = public_settings(mode)
    validate_config(load_config(settings))
    with pytest.raises(ConfigError, match=name):
        validate_config(load_config(settings | {name: invalid}))


def test_test_database_is_isolated() -> None:
    config = load_config(
        {"APP_ENV": "test", "DATABASE_URL": "sqlite:///development.db"}
    )
    assert config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
