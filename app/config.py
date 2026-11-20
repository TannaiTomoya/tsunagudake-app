from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DEVELOPMENT_KEY = "development-only-not-for-public-use"


class ConfigError(ValueError):
    """値や接続先を含めず、設定項目の名前だけで知らせます。"""


class Config:
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True


class Development(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class Test(Config):
    TESTING = True
    SESSION_COOKIE_SECURE = False


class Demo(Config):
    pass


class Production(Config):
    pass


CONFIGS = {
    "development": Development,
    "test": Test,
    "demo": Demo,
    "production": Production,
}


def boolean(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name, "").strip()
    if not value:
        return default
    if value not in {"0", "1"}:
        raise ConfigError(f"{name} は 0 または 1 を指定してください。")
    return value == "1"


def region_threshold(environ: Mapping[str, str]) -> int:
    value = environ.get("REGION_K_ANONYMITY", "").strip()
    if not value:
        return 5
    try:
        threshold = int(value)
    except ValueError:
        raise ConfigError(
            "REGION_K_ANONYMITY は 5 以上の整数を指定してください。"
        ) from None
    if threshold < 5:
        raise ConfigError("REGION_K_ANONYMITY は 5 以上の整数を指定してください。")
    return threshold


def positive_integer(
    environ: Mapping[str, str], name: str, default: int, *, maximum: int
) -> int:
    raw = environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = 0
    if not 1 <= value <= maximum:
        raise ConfigError(f"{name} は 1〜{maximum} の整数を指定してください。")
    return value


def load_config(environ: Mapping[str, str]) -> dict[str, Any]:
    mode = environ.get("APP_ENV", "production")
    if mode not in CONFIGS:
        raise ConfigError("APP_ENV の指定が正しくありません。")
    defaults = CONFIGS[mode]
    local = mode in {"development", "test"}
    if mode == "test":
        database = environ.get("TEST_DATABASE_URL") or "sqlite:///:memory:"
    else:
        database = environ.get("DATABASE_URL") or "sqlite:///tsunagudake.sqlite"
    return {
        "APP_ENV": mode,
        "PLEDGE_VALID_DAYS": positive_integer(
            environ, "PLEDGE_VALID_DAYS", 365, maximum=365
        ),
        "RATE_LIMIT_ENABLED": boolean(environ, "RATE_LIMIT_ENABLED", True),
        "RATE_LOGIN_FAIL_MAX": positive_integer(
            environ, "RATE_LOGIN_FAIL_MAX", 5, maximum=1000
        ),
        "RATE_LOGIN_FAIL_SECONDS": positive_integer(
            environ, "RATE_LOGIN_FAIL_SECONDS", 900, maximum=43200
        ),
        "RATE_LOGIN_IP_MAX": positive_integer(
            environ, "RATE_LOGIN_IP_MAX", 20, maximum=1000
        ),
        "REGION_K_ANONYMITY": region_threshold(environ),
        "DEBUG": defaults.DEBUG,
        "TESTING": defaults.TESTING,
        "SECRET_KEY": environ.get("SECRET_KEY") or (DEVELOPMENT_KEY if local else ""),
        "SQLALCHEMY_DATABASE_URI": database,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True, "pool_recycle": 1800},
        "SESSION_COOKIE_SECURE": boolean(
            environ, "SESSION_COOKIE_SECURE", defaults.SESSION_COOKIE_SECURE
        ),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=8),
        "SESSION_REFRESH_EACH_REQUEST": False,
        "TRUSTED_HOSTS": [
            host.strip()
            for host in environ.get(
                "TRUSTED_HOSTS", "localhost,127.0.0.1,[::1]" if local else ""
            ).split(",")
            if host.strip()
        ],
        "BASE_URL": environ.get("BASE_URL", "http://localhost:5000" if local else ""),
        "PROXY_FIX": boolean(environ, "PROXY_FIX", False),
        "DEMO_PASSWORD": environ.get("DEMO_PASSWORD", ""),
        "AI_DRIVER": environ.get("AI_DRIVER") or ("fake" if mode == "test" else "off"),
        # 台帳の控えを置くフォルダ（DB の外）。空なら instance/ledger-anchors。
        "LEDGER_ANCHOR_DIR": environ.get("LEDGER_ANCHOR_DIR", "").strip(),
        "MAX_CONTENT_LENGTH": 1024 * 1024,
    }


def validate_config(config: Mapping[str, Any]) -> None:
    threshold = config["REGION_K_ANONYMITY"]
    if type(threshold) is not int or threshold < 5:
        raise ConfigError("REGION_K_ANONYMITY は 5 以上の整数を指定してください。")
    mode = config["APP_ENV"]
    if mode not in CONFIGS:
        raise ConfigError("APP_ENV の指定が正しくありません。")
    driver = config["AI_DRIVER"]
    if driver not in {"off", "fake", "claude"}:
        raise ConfigError("AI_DRIVER の指定が正しくありません。")
    try:
        database = make_url(config["SQLALCHEMY_DATABASE_URI"])
    except ArgumentError, TypeError, ValueError:
        raise ConfigError("DATABASE_URL の形式が正しくありません。") from None
    if mode not in {"demo", "production"}:
        return

    problems = []
    key = config["SECRET_KEY"]
    placeholder = str(key).lower().replace("_", "-")
    if (
        not isinstance(key, str)
        or len(key) < 32
        or any(
            word in placeholder
            for word in (
                "development-only",
                "changeme",
                "change-me",
                "replace-me",
                "your-secret",
            )
        )
    ):
        problems.append("SECRET_KEY（32文字以上の固有の値）")
    if database.drivername != "mysql+pymysql" or not database.database:
        problems.append("DATABASE_URL（mysql+pymysql のデータベース）")
    try:
        base = urlsplit(config["BASE_URL"])
        valid_base = (
            base.scheme == "https" and bool(base.hostname) and not base.username
        )
    except ValueError:
        valid_base = False
    if not valid_base:
        problems.append("BASE_URL（https の URL）")
    hosts = config["TRUSTED_HOSTS"]
    if not hosts or any(not h.strip() or "*" in h or "/" in h for h in hosts):
        problems.append("TRUSTED_HOSTS（許可するホスト名）")
    if config["SESSION_COOKIE_SECURE"] is not True:
        problems.append("SESSION_COOKIE_SECURE（1）")
    if mode == "demo" and len(config["DEMO_PASSWORD"]) < 12:
        problems.append("DEMO_PASSWORD（12文字以上）")
    if mode == "production" and driver == "fake":
        problems.append("AI_DRIVER（production では fake は使えません）")
    if config["DEBUG"] or config["TESTING"]:
        problems.append("DEBUG・TESTING（公開環境では無効）")
    if problems:
        raise ConfigError(
            "起動できません。設定を確認してください：" + "、".join(problems)
        )
