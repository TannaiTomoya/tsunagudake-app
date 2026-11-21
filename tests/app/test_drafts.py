import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import _agree, sign_in, token
from test_identity import identity_app as identity_app

from app.extensions import db
from app.guide_faq import (
    BOUNDARY_ANSWER,
    NO_ANSWER,
    TOPIC_FAQS,
    may_send_out,
    nearest,
)
from app.models import (
    AiOutcome,
    AiPurpose,
    AiRun,
    Message,
    Visit,
    today_in_japan,
)
from app.services import ai

# 名前・金額・連絡先は、すべて架空。
MEMO = "来週の訪問を火曜にしたい。田中様 090-1234-5678 に 50,000円"
SECRETS = ("田中", "090", "1234", "50,000")
IDEA = "架空の気づき：田中様の箱を、月ごとに分ける"
DRAFT = "書いたメモから下書きを作る"
SUMMARY = "まとめの下書きを作る"


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def driver(purpose: AiPurpose, system: str, text: str) -> ai.Reply:
        sent.append((system, text))
        return ai._fake(purpose, system, text)

    monkeypatch.setitem(ai.DRIVERS, "fake", driver)
    monkeypatch.setattr(ai, "_answers", {})
    return sent


def _never(monkeypatch: pytest.MonkeyPatch) -> None:
    def driver(purpose: AiPurpose, system: str, text: str) -> ai.Reply:
        raise AssertionError("外を呼んではいけない")

    for name in ("fake", "claude"):
        monkeypatch.setitem(ai.DRIVERS, name, driver)


def _runs(app: Flask) -> list[AiRun]:
    with app.app_context():
        return list(db.session.scalars(select(AiRun).order_by(AiRun.id)))


def _message(client: FlaskClient, path: str, action: str, body: str) -> tuple[int, str]:
    data = {
        "csrf_token": token(client, "/account"),
        "action": action,
        "body": body,
        "template": "schedule",
    }
    response = client.post(path + "/messages", data=data)
    return response.status_code, response.get_data(as_text=True)


def _count(app: Flask) -> int:
    with app.app_context():
        return len(db.session.scalars(select(Message)).all())


def test_t65_message_draft_fills_the_box_and_a_person_sends(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = identity_app
    sent = _spy(monkeypatch)
    office, talent, office_path, talent_path = _agree(app)
    assert _message(talent, talent_path, "send", "過去の連絡：秘密の話")[0] == 302
    assert DRAFT in office.get(office_path + "/messages").get_data(as_text=True)

    status, page = _message(office, office_path, "draft", MEMO)
    assert status == 200 and "data-auto-draft" in page
    assert "自動で作った下書きです。" in page and "送るのは人です。" in page
    assert f">{ai.FAKE_REPLIES[AiPurpose.DRAFT]}</textarea>" in page

    # 送るのは、入力欄のメモを伏せた後の文と、ひな形の名前だけ。
    (system, text) = sent[0]
    assert system == ai.DRAFT_SYSTEM
    assert "用件:訪問の日程" in text and "〈伏せ〉" in text and "火曜" in text
    for secret in (*SECRETS, "秘密の話", "機密の本文", "架空利用者", "架空事務所"):
        assert secret not in text and secret not in system

    # 下書きは、送られていない。記録に本文は無い。
    assert _count(app) == 1
    (run,) = _runs(app)
    assert (run.purpose, run.outcome) == (AiPurpose.DRAFT, AiOutcome.OK)
    assert (run.actor_user_id, run.office_id, run.result) == (1, 1, None)

    # つなぎ手も作れる。費用は、その伴走の事務所に数える。
    status, page = _message(talent, talent_path, "draft", "了解しました、と返したい")
    assert status == 200 and "data-auto-draft" in page
    assert [(run.actor_user_id, run.office_id) for run in _runs(app)][1] == (2, 1)
    assert _count(app) == 1

    # 送るのは、これまでどおり人が確かめてから。
    draft = ai.FAKE_REPLIES[AiPurpose.DRAFT]
    assert _message(office, office_path, "confirm", draft)[0] == 200
    assert _count(app) == 1
    assert _message(office, office_path, "send", draft)[0] == 302
    assert _count(app) == 2
    assert _message(office, office_path, "auto", draft)[0] == 400

    # off では、ボタンが出ず、外も呼ばない。
    app.config["AI_DRIVER"] = "off"
    _never(monkeypatch)
    assert DRAFT not in office.get(office_path + "/messages").get_data(as_text=True)
    status, page = _message(office, office_path, "draft", MEMO)
    assert status == 409 and "今は、下書きを作れません。" in page
    assert "data-auto-draft" not in page and "田中様" in page
    assert len(_runs(app)) == 2 and _count(app) == 2

    # 終わった伴走では、下書きも作らない。
    app.config["AI_DRIVER"] = "fake"
    end = {"csrf_token": token(talent, talent_path), "reason": "goal_met"}
    assert talent.post(talent_path + "/end", data=end).status_code == 302
    for client, path in ((office, office_path), (talent, talent_path)):
        assert _message(client, path, "draft", MEMO)[0] == 409
    assert len(_runs(app)) == 2


def _ask(
    client: FlaskClient,
    question: str,
    page: str = "/account",
    topic: str = "write",
) -> dict[str, str]:
    response = client.post(
        "/guide/ask",
        data={
            "csrf_token": token(client, page),
            "topic": topic,
            "question": question,
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    reply = response.get_json()
    assert isinstance(reply, dict)
    return reply


def test_t65_free_question_goes_out_only_when_signed_in_and_safe(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = identity_app
    sent = _spy(monkeypatch)
    question = "あさってのお昼は田中様と何を食べますか 090-1234-5678"
    assert may_send_out(question) and len(question) <= 100

    # 未ログインの人の質問は、送らない。
    anonymous = app.test_client()
    assert _ask(anonymous, question, "/")["answer"] == NO_ANSWER
    assert sent == []

    client = app.test_client()
    assert sign_in(client, "office@example.org") == 302
    # 用意した答えがあれば、それを返す。税務の言葉がある質問は、送らない。
    assert _ask(client, "料金はいくらですか", topic="time_money")["kind"] == "faq"
    assert _ask(client, "税務相談はできますか", topic="connector")["answer"] == (
        BOUNDARY_ANSWER
    )
    for taxing in ("この経費は落とせますか", "控除のことを教えて", "しんこく 申告"):
        assert not may_send_out(taxing)
        assert _ask(client, taxing, topic="time_money")["kind"] != "auto"
    assert not may_send_out("") and not may_send_out("ぜいむ", ("ぜいむ",))
    assert sent == [] and _runs(app) == []

    # 用意した答えに無い質問だけ、伏せてから聞く。「自動で作った答え」と書いて返す。
    # 材料は、選んだ話題の答えだけ。
    reply = _ask(client, question, topic="write")
    assert reply["kind"] == "auto"
    assert reply["answer"] == ai.AUTO_ANSWER + ai.FAKE_REPLIES[AiPurpose.QUESTION]
    (system, text) = sent[0]
    assert "〈伏せ〉" in text and "食べますか" in text
    for secret in ("田中", "090", "1234", "架空利用者", "架空事務所"):
        assert secret not in text and secret not in system
    assert system.startswith(ai.QUESTION_SYSTEM) and system.count("問：") == 3
    for faq in nearest(question, topic="write"):
        assert faq.answer in system
    for faq in TOPIC_FAQS["time_money"]:
        assert faq.answer not in system
    (run,) = _runs(app)
    assert (run.purpose, run.office_id, run.actor_user_id) == (
        AiPurpose.QUESTION,
        None,
        1,
    )
    assert run.result is None and run.need_id is None

    # 同じ質問には、外を呼ばずに前の答えを返す。
    assert _ask(client, question, topic="write") == reply
    assert len(sent) == 1 and len(_runs(app)) == 1

    # JavaScript が無いときの画面にも、同じ答えが出る。
    form = {
        "csrf_token": token(client, "/account"),
        "topic": "write",
        "question": question,
    }
    page = client.post("/guide/ask", data=form).get_data(as_text=True)
    assert ai.AUTO_ANSWER in page and 'data-kind="auto"' in page

    # off では、これまでどおり用意した答えだけ。
    app.config["AI_DRIVER"] = "off"
    _never(monkeypatch)
    assert (
        _ask(client, "まったく別の、あたらしい質問です", topic="write")["answer"]
        == NO_ANSWER
    )
    assert len(_runs(app)) == 1


def _summary(client: FlaskClient, office_path: str, month: str) -> tuple[int, str]:
    data = {"csrf_token": token(client, "/account"), "month": month}
    response = client.post(office_path + "/ideas/summary", data=data)
    return response.status_code, response.get_data(as_text=True)


def test_t65_idea_summary_is_shown_once_and_never_stored(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = identity_app
    sent = _spy(monkeypatch)
    office, talent, office_path, talent_path = _agree(app)
    month = today_in_japan().strftime("%Y-%m")

    # メモが無い月・形の違う月では、呼ばない。
    for bad in (month, "2000-01", "2026-13", ""):
        assert _summary(office, office_path, bad)[0] == 409
    assert sent == []
    assert SUMMARY not in office.get(office_path).get_data(as_text=True)

    visit = {
        "csrf_token": token(talent, talent_path + "/visits/new"),
        "day": today_in_japan().isoformat(),
        "minutes": "60",
        "category": "collect",
        "idea": IDEA,
    }
    assert talent.post(talent_path + "/visits/new", data=visit).status_code == 302
    # ボタンは、事務所の画面にだけ出る。
    assert SUMMARY in office.get(office_path).get_data(as_text=True)
    assert SUMMARY not in talent.get(talent_path).get_data(as_text=True)

    status, page = _summary(office, office_path, month)
    assert status == 200 and "data-auto-draft" in page
    assert ai.FAKE_REPLIES[AiPurpose.SUMMARY] in page
    assert "自動で作った下書きです。保存されません。" in page
    (system, text) = sent[0]
    assert system == ai.SUMMARY_SYSTEM and text.startswith("・")
    assert "箱を、月ごとに分ける" in text and "田中" not in text

    # まとめは、どこにも残さない。メモも変えない。
    with app.app_context():
        stored = db.session.scalar(select(Visit))
        assert stored is not None and stored.idea == IDEA
        assert stored.idea_adopted_at is None
    (run,) = _runs(app)
    assert (run.purpose, run.office_id, run.result) == (AiPurpose.SUMMARY, 1, None)
    assert ai.FAKE_REPLIES[AiPurpose.SUMMARY] not in office.get(office_path).get_data(
        as_text=True
    )

    # 事務所ごとの月の上限を超えたら、その事務所ではひな形だけ。ほかは止めない。
    app.config.update(
        AI_OFFICE_MONTHLY_LIMIT_YEN=1,
        AI_INPUT_YEN_PER_MTOK=1_000_000,
        AI_OUTPUT_YEN_PER_MTOK=1_000_000,
    )
    with app.app_context():
        assert ai.month_cost_yen(office_id=1) >= 1
        assert ai.month_cost_yen(office_id=2) == 0
        assert not ai.available(1) and ai.available(2) and ai.available()
    status, page = _summary(office, office_path, month)
    assert status == 409 and "今は、まとめの下書きを作れません。" in page
    assert DRAFT not in office.get(office_path + "/messages").get_data(as_text=True)
    assert _message(office, office_path, "draft", MEMO)[0] == 409
    assert len(sent) == 1 and len(_runs(app)) == 1

    # off では、ボタンが出ず、外も呼ばない。
    app.config.update(AI_OFFICE_MONTHLY_LIMIT_YEN=0, AI_DRIVER="off")
    _never(monkeypatch)
    assert SUMMARY not in office.get(office_path).get_data(as_text=True)
    assert _summary(office, office_path, month)[0] == 409
    assert len(_runs(app)) == 1


def test_t65_replies_are_cut_and_cleaned() -> None:
    # 返ってきた文は、制御文字を落とし、上限で切る。
    assert ai._plain("  あ\x00い\x1b[31mう\r\nえ\u200b  ", 10) == "あい[31mう\nえ"
    assert ai._plain("あ" * 500, ai.REPLY_MAX[AiPurpose.DRAFT]) == "あ" * 400
    assert ai._plain(" \n ", 10) == ""
    assert ai.REPLY_MAX == {
        AiPurpose.DRAFT: 400,
        AiPurpose.QUESTION: 160,
        AiPurpose.SUMMARY: 300,
    }
