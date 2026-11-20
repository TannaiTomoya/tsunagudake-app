from pathlib import Path
from xml.etree import ElementTree

import pytest
from flask import Flask, render_template
from flask.testing import FlaskClient

from app import create_app
from app.guide import SCRIPTS


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setenv("APP_ENV", "test")
    return create_app()


@pytest.mark.parametrize("endpoint", SCRIPTS)
def test_fixed_script_renders_without_javascript(app: Flask, endpoint: str) -> None:
    with app.test_request_context("/"):
        html = render_template("base.html", guide_endpoint=endpoint)
    guide = html.split('<details class="masu-guide">', 1)[1].split("</details>", 1)[0]
    assert 'aria-label="案内役のマス。この画面の説明を聞く"' in guide
    assert 'aria-live="polite"' in guide
    assert html.count("<script") == 2
    assert (
        '<script defer src="/static/vendor/animejs/anime-4.1.3.umd.min.js"></script>'
        in html
    )
    assert '<script defer src="/static/js/guide.js"></script>' in html
    assert 1 <= len(SCRIPTS[endpoint]) <= 3
    for line in SCRIPTS[endpoint]:
        assert 0 < len(line) <= 40
        assert f"<p>{line}</p>" in guide


@pytest.mark.parametrize(
    ("path", "endpoint"),
    [
        ("/", "public.index"),
        ("/how", "public.how"),
        ("/login", "auth.login"),
        ("/missing", "error"),
    ],
)
def test_guide_uses_local_assets_and_screen_script(
    app: Flask, path: str, endpoint: str
) -> None:
    client: FlaskClient = app.test_client()
    response = client.get(path)
    html = response.get_data(as_text=True)
    assert (
        response.headers["Content-Security-Policy"]
        == "default-src 'self'; frame-ancestors 'none'"
    )
    assert html.count("<script") == 2
    assert (
        '<script defer src="/static/vendor/animejs/anime-4.1.3.umd.min.js"></script>'
        in html
    )
    assert '<script defer src="/static/js/guide.js"></script>' in html
    assert SCRIPTS[endpoint][0] in html
    assert client.get("/static/js/guide.js").status_code == 200


@pytest.mark.parametrize(
    ("template", "first", "second"),
    [
        ("public/index.html", "hero-copy", "ledger-diagram"),
        ("needs/detail.html", "公開する", "翻訳の結果"),
        ("matching/candidates.html", ">声をかける</button>", "候補の一覧"),
        ("matching/engagement.html", "matching/activity.html", "matching/visits.html"),
        ("auth/talent_home.html", "あなたへの声かけ", "守秘の誓約と研修</h2>"),
    ],
)
def test_small_screen_decision_precedes_reference(
    template: str, first: str, second: str
) -> None:
    source = (Path("app/templates") / template).read_text()
    assert (
        source.index("decision-pane")
        < source.index(first)
        < source.index("reading-pane")
        < source.index(second)
    )


def test_visible_cautions_outside_guide() -> None:
    root = Path("app/templates")
    for template, text in [
        ("needs/form.html", "顧問先の名前と金額は書かないでください"),
        ("public/index.html", "数字は架空"),
        ("public/index.html", "減らなければ0円"),
        ("auth/region_home.html", "合計から逆算できる場合は、合計も伏せます"),
        (
            "matching/activity.html",
            "確認待ちの時間をすべて確かめると明細を確定できます",
        ),
    ]:
        assert text in (root / template).read_text()


def test_mascot_parts_and_vendor_files(app: Flask) -> None:
    with app.test_request_context("/"):
        drawing = ElementTree.fromstring(render_template("guide/masu.svg"))
    classes = [e.get("class") for e in drawing.iter()]
    assert classes.count("eye-open") == 2
    assert classes.count("eye-glint") == 2
    assert {"masu-body", "masu-fold", "masu-mouth", "masu-shadow"}.issubset(classes)
    vendor = Path("app/static/vendor/animejs")
    assert (vendor / "anime-4.1.3.umd.min.js").stat().st_size > 0
    assert "MIT" in (vendor / "LICENSE").read_text()


def test_mascot_error_state_and_static_fallback(app: Flask) -> None:
    client = app.test_client()
    assert 'data-mood="worried"' in client.get("/missing").get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    assert 'data-mood="idle"' in home
    assert home.count('<svg class="masu"') == 2
    assert '<details class="masu-guide">' in home
