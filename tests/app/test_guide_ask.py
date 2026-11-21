import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.guide import SCRIPTS
from app.guide_faq import (
    ANSWER_LIMIT,
    BOUNDARY_ANSWER,
    BY_KEY,
    FAQS,
    NO_ANSWER,
    SCREEN_KEYS,
    TOPIC_BY_KEY,
    TOPIC_FAQS,
    TOPIC_REQUIRED,
    TOPICS,
    answer_key,
    answer_text,
    nearest,
    suggestions,
)
from tsunagu_rules.translation import load_dictionary

VOICES = ("off", "poko", "piko", "koto", "read")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setenv("APP_ENV", "test")
    return create_app()


def _token(client: FlaskClient, path: str = "/") -> str:
    html = client.get(path).get_data(as_text=True)
    found = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert found
    return found.group(1)


def test_prepared_answers_are_short_and_unique() -> None:
    assert 20 <= len(FAQS) <= 30
    assert len(BY_KEY) == len(FAQS)
    for faq in FAQS:
        assert 0 < len(faq.answer) <= ANSWER_LIMIT
        assert faq.answer.count("。") <= 3
        assert "AI" not in faq.answer
    assert len(NO_ANSWER) <= ANSWER_LIMIT
    assert len(BOUNDARY_ANSWER) <= ANSWER_LIMIT


def test_every_screen_offers_three_known_questions() -> None:
    assert [q.key for q in suggestions("needs.new")] == [
        "need_example",
        "need_hours",
        "names",
    ]
    assert set(SCREEN_KEYS) <= set(SCRIPTS)
    for screen in [*SCRIPTS, "unknown.screen", None]:
        offered = suggestions(screen)
        assert len(offered) == 3
        assert len({faq.key for faq in offered}) == 3
    assert "zero" not in {faq.key for faq in suggestions("public.index", "zero")}


@pytest.mark.parametrize(
    ("question", "topic", "key"),
    [
        ("減らなかったらどうなるの", "time_money", "zero"),
        ("料金はいくらですか", "time_money", "fee"),
        ("点数の決め方を知りたい", "connector", "score"),
        ("台帳は書き換えられますか？", "ledger", "ledger"),
        ("困りごとの例が浮かばない", "write", "need_example"),
        ("時間がわからない", "time_money", "need_hours"),
        ("顧問先のデータを持ち出しませんか", "ledger", "data"),
    ],
)
def test_free_question_finds_the_nearest_prepared_answer(
    question: str, topic: str, key: str
) -> None:
    first = answer_text(question, topic=topic)
    assert (first.kind, first.key) == ("faq", key)
    assert answer_text(question, topic=topic) == first


def test_every_faq_belongs_to_exactly_one_topic() -> None:
    assert len(TOPICS) == 4
    assert set(TOPIC_BY_KEY) == {"write", "time_money", "connector", "ledger"}
    claimed = [key for topic in TOPICS for key in topic.faq_keys]
    assert sorted(claimed) == sorted(BY_KEY)
    assert len(claimed) == len(set(claimed)) == len(FAQS)


def test_same_question_stays_inside_the_chosen_topic() -> None:
    question = "料金はいくらですか"
    hit = answer_text(question, topic="time_money")
    assert (hit.kind, hit.key) == ("faq", "fee")
    for other in ("write", "connector", "ledger"):
        miss = answer_text(question, topic=other)
        assert miss.kind == "none"
        assert miss.answer == NO_ANSWER
        assert miss.key is None
        for faq in nearest(question, topic=other):
            assert faq.key in {item.key for item in TOPIC_FAQS[other]}
            assert faq.key != "fee"


def test_unrelated_or_empty_question_has_no_answer() -> None:
    assert answer_text("料金はいくらですか").answer == TOPIC_REQUIRED
    assert answer_text("料金はいくらですか", topic="unknown").answer == TOPIC_REQUIRED
    for question in ("", "   ", "今日の天気を教えて", "x" * 400):
        reply = answer_text(question, topic="write")
        assert (reply.kind, reply.answer, reply.key) == ("none", NO_ANSWER, None)
    assert answer_key("missing").kind == "none"


def test_tax_judgement_is_stopped_before_matching() -> None:
    taxwork = load_dictionary().taxwork
    reply = answer_text(
        "料金の中で申告書の作成もお願いできますか", taxwork, topic="time_money"
    )
    assert (reply.kind, reply.answer) == ("boundary", BOUNDARY_ANSWER)
    assert answer_text("料金はいくらですか", taxwork, topic="time_money").kind == "faq"
    assert answer_text("申告書の作成は頼めますか", taxwork).kind == "boundary"


def test_guide_shows_voices_and_questions_without_javascript(app: Flask) -> None:
    client = app.test_client()
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert (
        response.headers["Content-Security-Policy"]
        == "default-src 'self'; frame-ancestors 'none'"
    )
    voice = re.search(r'<select aria-label="案内役の声">(.*?)</select>', html, re.S)
    assert voice
    assert re.findall(r'<option value="(\w+)">', voice.group(1)) == list(VOICES)
    for name in ("guide-voice.js", "guide.js", "guide-ask.js"):
        assert f'<script defer src="/static/js/{name}"></script>' in html
        assert client.get(f"/static/js/{name}").status_code == 200
    assert not re.search(r'<script[^>]+src="https?:', html)
    assert '<link rel="stylesheet" href="/static/css/guide-ask.css">' in html
    assert html.count('name="faq"') == 3
    for faq in suggestions("public.index"):
        assert f'value="{faq.key}"' in html
        assert faq.question in html
    assert "先に話題を選んでください。" in html
    assert 'name="topic"' in html
    assert html.count("<option") >= 5
    for topic in TOPICS:
        assert f'value="{topic.key}"' in html
        assert topic.label in html
    assert "顧問先の名前と金額は書かないでください。" in html
    assert 'maxlength="100"' in html


def test_voice_is_made_on_the_device_only() -> None:
    source = Path("app/static/js/guide-voice.js").read_text(encoding="utf-8")
    for mode in VOICES[1:4]:
        assert f"{mode}:" in source
    for outside in ("fetch(", "XMLHttpRequest", "http://", "https://", "import("):
        assert outside not in source


def test_chosen_question_returns_prepared_answer_as_json(app: Flask) -> None:
    client = app.test_client()
    response = client.post(
        "/guide/ask",
        data={"csrf_token": _token(client), "screen": "public.index", "faq": "zero"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["kind"] == "faq"
    assert body["answer"] == BY_KEY["zero"].answer
    assert len(body["next"]) == 3
    assert "zero" not in {item["key"] for item in body["next"]}


def test_free_question_is_answered_and_not_echoed_in_json(app: Flask) -> None:
    client = app.test_client()
    response = client.post(
        "/guide/ask",
        data={
            "csrf_token": _token(client),
            "screen": "<script>",
            "topic": "time_money",
            "question": "申告書の作成は頼めますか",
        },
        headers={"Accept": "application/json"},
    )
    body = response.get_json()
    assert (body["kind"], body["answer"]) == ("boundary", BOUNDARY_ANSWER)
    assert set(body) == {"answer", "kind", "next"}
    assert [item["key"] for item in body["next"]] == [
        faq.key for faq in suggestions(None, "tax")
    ]


def test_free_question_without_topic_asks_to_choose(app: Flask) -> None:
    client = app.test_client()
    response = client.post(
        "/guide/ask",
        data={
            "csrf_token": _token(client),
            "screen": "public.index",
            "question": "料金はいくらですか",
        },
        headers={"Accept": "application/json"},
    )
    body = response.get_json()
    assert (body["kind"], body["answer"]) == ("none", TOPIC_REQUIRED)


def test_answer_page_works_without_javascript(app: Flask) -> None:
    client = app.test_client()
    response = client.post(
        "/guide/ask",
        data={
            "csrf_token": _token(client),
            "screen": "public.index",
            "topic": "write",
            "question": "<b>今日の天気</b>",
        },
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert NO_ANSWER in html
    assert "<b>今日の天気</b>" not in html
    assert "&lt;b&gt;今日の天気&lt;/b&gt;" in html
    assert html.count('name="faq"') == 3
    assert 'value="write" selected' in html or "selected>困りごとの書き方" in html


def test_ask_requires_csrf_token_and_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    assert client.post("/guide/ask", data={"faq": "zero"}).status_code == 400
    assert client.get("/guide/ask").status_code == 405
