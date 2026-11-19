from unittest.mock import patch

import pytest
from flask.testing import FlaskClient
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    monkeypatch.setenv("APP_ENV", "test")
    return create_app().test_client()


def test_t32_health_and_headers(client: FlaskClient) -> None:
    response = client.get("/healthz", base_url="https://localhost")
    assert response.status_code == 200
    assert response.data == b"ok"
    assert response.mimetype == "text/plain"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "max-age=" in response.headers["Strict-Transport-Security"]


def test_t32_database_failure_is_unavailable(client: FlaskClient) -> None:
    with patch.object(
        db.session, "execute", side_effect=OperationalError(None, None, OSError())
    ):
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.data == b"unavailable"


def test_t32_bad_host(client: FlaskClient) -> None:
    assert client.get("/healthz", base_url="http://unknown.invalid").status_code == 400


def test_home_uses_local_styles(client: FlaskClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "税理士は、判断するだけ。" in response.get_data(as_text=True).replace(
        "<wbr>", ""
    )
    for path in ("/static/vendor/pico.min.css", "/static/css/theme.css"):
        assert path in response.get_data(as_text=True)
        assert client.get(path).status_code == 200


@pytest.mark.parametrize(
    ("path", "title", "status"),
    [
        ("/", "経理アプリ-Tsunagudake-", 200),
        ("/how", "しくみ｜経理アプリ-Tsunagudake-", 200),
        ("/login", "ログイン｜経理アプリ-Tsunagudake-", 200),
        ("/missing", "確認してください｜経理アプリ-Tsunagudake-", 404),
    ],
)
def test_branded_pages(client: FlaskClient, path: str, title: str, status: int) -> None:
    response = client.get(path)
    assert response.status_code == status
    html = response.get_data(as_text=True)
    assert f"<title>{title}</title>" in html
    assert 'aria-label="メインメニュー"' in html
    assert "/demo/enter" not in html
