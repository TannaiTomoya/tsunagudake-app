from struct import unpack
from unittest.mock import patch
from urllib.parse import urljoin

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
    for phrase in (
        "資料回収の試算例（数字は架空）",
        "顧問先の相談に",
        "減らなければ0円",
    ):
        assert phrase in response.get_data(as_text=True)
    for path in ("/static/vendor/pico.min.css", "/static/css/theme.css"):
        assert path in response.get_data(as_text=True)
        assert client.get(path).status_code == 200


@pytest.mark.parametrize(
    ("path", "title", "status"),
    [
        ("/", "ホーム｜つなぐだけ", 200),
        ("/how", "しくみ｜つなぐだけ", 200),
        ("/login", "ログイン｜つなぐだけ", 200),
        ("/missing", "確認してください｜つなぐだけ", 404),
    ],
)
def test_branded_pages(client: FlaskClient, path: str, title: str, status: int) -> None:
    response = client.get(path)
    assert response.status_code == status
    html = response.get_data(as_text=True)
    assert f"<title>{title}</title>" in html
    assert 'aria-label="メインメニュー"' in html
    assert "/demo/enter" not in html
    if path == "/how":
        assert "登録ID" not in html
        assert "先に登録された人" in html
    if path == "/missing":
        assert "ホームから開き直してください" in html


def test_masu_app_icons(client: FlaskClient) -> None:
    manifest_path = "/static/manifest.json"
    apple_icon = "/static/icons/masu-180.png"
    for page in ("/", "/login"):
        html = client.get(page).get_data(as_text=True)
        assert f'rel="manifest" href="{manifest_path}"' in html
        assert f'rel="apple-touch-icon" sizes="180x180" href="{apple_icon}"' in html
        assert 'rel="icon" href="/favicon.ico"' in html

    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 200
    assert favicon.data[:4] == b"\x00\x00\x01\x00"
    manifest = client.get(manifest_path).get_json()
    assert manifest["name"] == manifest["short_name"] == "つなぐだけ"
    assert manifest["display"] == "standalone"
    assert urljoin(manifest_path, manifest["start_url"]) == "/"
    assert urljoin(manifest_path, manifest["scope"]) == "/"

    assert {(icon["purpose"], icon["sizes"]) for icon in manifest["icons"]} == {
        ("any", "192x192"),
        ("any", "512x512"),
        ("maskable", "512x512"),
    }
    for icon in [*manifest["icons"], {"src": apple_icon, "sizes": "180x180"}]:
        png = client.get(urljoin(manifest_path, icon["src"]))
        assert png.status_code == 200
        assert png.mimetype == "image/png"
        assert png.data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = unpack(">II", png.data[16:24])
        assert f"{width}x{height}" == icon["sizes"]
