from datetime import date, timedelta
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import PASSWORD, _write_need, sign_in, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    AiOutcome,
    AiPurpose,
    AiRun,
    AuditLog,
    MemberRole,
    Need,
    NeedStatus,
    NeedTranslation,
    OfficeMember,
    Role,
    TranslationSource,
    User,
    utc_now,
)
from app.services import ai
from tsunagu_rules.types import NeedKind

# 規則の辞書に当たらない文。名前・社名・金額・連絡先は、すべて架空。
BODY = (
    "うまく言えないが、毎月ばたばたしている。"
    "架空商事株式会社の田中さんに 090-1234-5678 で 320,000円。tanaka@example.org"
)
SECRETS = ("田中", "架空商事", "090", "1234", "320,000", "example.org")
ASK = "型の候補を出してもらう"


def _owner(app: Flask) -> FlaskClient:
    client = app.test_client()
    assert sign_in(client, "office@example.org") == 302
    return client


def _post(client: FlaskClient, path: str, action: str, **data: str) -> int:
    form = {"csrf_token": token(client, "/account"), **data}
    return client.post(f"{path}/{action}", data=form).status_code


def _runs(app: Flask) -> list[AiRun]:
    with app.app_context():
        return list(db.session.scalars(select(AiRun).order_by(AiRun.id)))


def _translations(app: Flask) -> list[NeedTranslation]:
    with app.app_context():
        return list(db.session.scalars(select(NeedTranslation)))


def _spy(monkeypatch: pytest.MonkeyPatch, reply: str | None = None) -> list[str]:
    sent: list[str] = []

    def driver(purpose: AiPurpose, system: str, text: str) -> ai.Reply:
        sent.append(text)
        if reply is None:
            return ai._fake(purpose, system, text)
        return ai.Reply(reply, 10, 5)

    monkeypatch.setitem(ai.DRIVERS, "fake", driver)
    return sent


def _never(monkeypatch: pytest.MonkeyPatch) -> None:
    def driver(purpose: AiPurpose, system: str, text: str) -> ai.Reply:
        raise AssertionError("外を呼んではいけない")

    for name in ("fake", "claude"):
        monkeypatch.setitem(ai.DRIVERS, name, driver)


@pytest.mark.parametrize("driver", ["off", "claude"])
def test_t64_off_and_unconfigured_claude_never_call_out(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch, driver: str
) -> None:
    # off（既定）と、設定のそろっていない claude は、どこからも外を呼ばない。
    identity_app.config["AI_DRIVER"] = driver
    _never(monkeypatch)
    client = _owner(identity_app)
    path = _write_need(client, "/o/1/needs/new", BODY)
    page = client.get(path).get_data(as_text=True)
    assert "規則では訳せませんでした。" in page and ASK not in page
    assert _post(client, path, "suggest") == 409
    assert _post(client, path, "accept-kind", kind="tooling") == 409
    assert _runs(identity_app) == [] and _translations(identity_app) == []
    with identity_app.app_context():
        assert not ai.available()


def test_t64_candidates_are_masked_listed_and_decided_by_the_owner(
    database_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _spy(monkeypatch)
    client = _owner(database_app)
    path = _write_need(client, "/o/1/needs/new", BODY)
    assert ASK in client.get(path).get_data(as_text=True)
    assert _post(client, path, "suggest") == 302

    # 送るのは、伏せた後の文だけ。
    assert len(sent) == 1 and "〈伏せ〉" in sent[0] and "ばたばた" in sent[0]
    for secret in SECRETS:
        assert secret not in sent[0]

    # 記録には、本文も返ってきた文も無い。Enum に無い値（audit）は捨てる。
    columns = set(AiRun.__table__.columns.keys())
    assert not columns & {"body", "text", "prompt", "reply", "request", "response"}
    (run,) = _runs(database_app)
    assert (run.purpose, run.outcome) == (AiPurpose.TRANSLATE, AiOutcome.OK)
    assert (run.result, run.chars, run.driver) == ("tooling", len(sent[0]), "fake")
    assert (run.actor_user_id, run.office_id, run.need_id) == (1, 1, 1)
    assert run.input_tokens > 0 and run.output_tokens > 0

    # 候補は出るだけ。まだ下書きで、型も付いていない。
    page = client.get(path).get_data(as_text=True)
    assert "「道具の設定」にする" in page and "audit" not in page
    assert ASK not in page
    assert _translations(database_app) == []

    # 職員には、頼むボタンも候補も出さない（決めるのは代表）。
    with database_app.app_context():
        staff = User(
            email="staff@example.org", role=Role.OFFICE, display_name="架空職員"
        )
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.flush()
        db.session.add(
            OfficeMember(user_id=staff.id, office_id=1, member_role=MemberRole.STAFF)
        )
        db.session.commit()
    other = database_app.test_client()
    assert sign_in(other, "staff@example.org") == 302
    seen = other.get(path).get_data(as_text=True)
    assert "規則では訳せませんでした。" in seen
    assert ASK not in seen and "にする</button>" not in seen

    # 同じ文には、もう一度は聞かない。
    assert _post(client, path, "suggest") == 302
    assert len(sent) == 1 and len(_runs(database_app)) == 1

    # 採れるのは、サーバーに残した候補の型だけ。
    for kind in ("collect", "audit", ""):
        assert _post(client, path, "accept-kind", kind=kind) == 409
    assert _translations(database_app) == []

    assert _post(client, path, "accept-kind", kind="tooling") == 302
    rows = _translations(database_app)
    assert rows and {row.category for row in rows} == {NeedKind.TOOLING}
    assert {row.source for row in rows} == {TranslationSource.AI_ACCEPTED}
    with database_app.app_context():
        need = db.session.get(Need, 1)
        assert need is not None and need.status == NeedStatus.DRAFT
        assert need.published_at is None
        actions = {
            row.action: row.actor_user_id
            for row in db.session.scalars(select(AuditLog))
            if row.action.startswith("need_kind_")
        }
    assert actions == {"need_kind_suggested": 1, "need_kind_accepted": 1}

    # 採ったあとは、もう採れない。公開は、これまでどおり代表が別に押す。
    assert _post(client, path, "accept-kind", kind="tooling") == 409
    assert _post(client, path, "publish") == 302


def test_t64_rewriting_drops_the_candidates_and_rule_cases_are_not_sent(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _spy(monkeypatch)
    client = _owner(identity_app)
    path = _write_need(client, "/o/1/needs/new", BODY)
    assert _post(client, path, "suggest") == 302
    assert len(sent) == 1

    # 同じ文のまま手で型を選んだら、候補はもう採れない（型を混ぜない）。
    _write_need(client, f"{path}/edit", BODY, "collect")
    assert _post(client, path, "accept-kind", kind="tooling") == 409
    rows = _translations(identity_app)
    assert rows and {row.source for row in rows} == {TranslationSource.MANUAL}
    assert {row.category for row in rows} == {NeedKind.COLLECT}

    # 書き直すと、前の文の候補は使えない。
    _write_need(client, f"{path}/edit", "別の言い方で、毎月ばたばたしている。")
    page = client.get(path).get_data(as_text=True)
    assert "にする</button>" not in page and ASK in page
    assert _post(client, path, "accept-kind", kind="tooling") == 409
    assert _translations(identity_app) == []

    # 規則で訳せた困りごと・税理士業務の語で止まった困りごとは、送らない。
    ruled = _write_need(client, "/o/1/needs/new", "資料が集まらない。")
    blocked = _write_need(client, "/o/1/needs/new", "うまく言えないが、税務相談。")
    for other in (ruled, blocked):
        assert ASK not in client.get(other).get_data(as_text=True)
        assert _post(client, other, "suggest") == 409
        assert _post(client, other, "accept-kind", kind="tooling") == 409
    assert len(sent) == 1 and len(_runs(identity_app)) == 1


def test_t64_monthly_limit_and_daily_count_stop_the_calls(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _spy(monkeypatch)
    client = _owner(identity_app)
    first = _write_need(client, "/o/1/needs/new", BODY)
    second = _write_need(client, "/o/1/needs/new", "ほかにも、毎月ばたばたしている。")

    # 先月までの分は、今月の費用に数えない。
    with identity_app.app_context():
        db.session.add(
            AiRun(
                actor_user_id=1,
                purpose=AiPurpose.TRANSLATE,
                driver="fake",
                chars=1,
                input_tokens=9_000_000,
                output_tokens=9_000_000,
                outcome=AiOutcome.EMPTY,
                created_at=utc_now() - timedelta(days=70),
            )
        )
        db.session.commit()
    identity_app.config.update(
        AI_MONTHLY_LIMIT_YEN=1,
        AI_INPUT_YEN_PER_MTOK=1_000_000,
        AI_OUTPUT_YEN_PER_MTOK=1_000_000,
    )
    with identity_app.app_context():
        assert ai.month_cost_yen() == 0 and ai.available()
    assert _post(client, first, "suggest") == 302
    with identity_app.app_context():
        assert ai.month_cost_yen() >= 1 and not ai.available()
        # 月は日本時間で区切る。次の月になれば、また 0 から。
        assert ai.month_cost_yen(date(2999, 1, 1)) == 0

    # 月の上限を超えたら、off と同じ。
    assert ASK not in client.get(second).get_data(as_text=True)
    assert _post(client, second, "suggest") == 409
    assert len(sent) == 1

    # 上限が無くても、1人が24時間に呼べる回数には限りがある。
    identity_app.config["AI_MONTHLY_LIMIT_YEN"] = 0
    monkeypatch.setattr(ai, "PERSON_DAILY_MAX", 1)
    assert _post(client, second, "suggest") == 409
    assert len(sent) == 1 and len(_runs(identity_app)) == 2


def test_t64_failed_and_useless_replies_offer_nothing(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _owner(identity_app)
    path = _write_need(client, "/o/1/needs/new", BODY)

    def broken(purpose: AiPurpose, system: str, text: str) -> ai.Reply:
        raise OSError("架空の障害")

    monkeypatch.setitem(ai.DRIVERS, "fake", broken)
    form = {"csrf_token": token(client, "/account")}
    response = client.post(f"{path}/suggest", data=form)
    assert response.status_code == 409
    assert "返事がありませんでした" in response.get_data(as_text=True)
    (run,) = _runs(identity_app)
    assert (run.outcome, run.input_tokens, run.result) == (AiOutcome.ERROR, 0, None)
    assert _post(client, path, "accept-kind", kind="tooling") == 409

    # 型の値が入っていない返事は、候補にしない。
    # 返ってきた文は、画面にも記録にも出さない。
    _spy(monkeypatch, "道具の設定だと思います。<b>tooling</b>")
    assert _post(client, path, "suggest") == 302
    page = client.get(path).get_data(as_text=True)
    assert "型の候補は出ませんでした。" in page and "だと思います" not in page
    assert _runs(identity_app)[-1].outcome == AiOutcome.EMPTY
    assert _runs(identity_app)[-1].result is None
    assert _post(client, path, "accept-kind", kind="tooling") == 409
    assert _translations(identity_app) == []

    # 多くても2つ。同じ値・Enum に無い値・文字でない値は捨てる。
    reply = '説明 ["tooling", "tooling", 3, "audit", "check", "entry"] 以上'
    assert ai._kinds(reply) == [NeedKind.TOOLING, NeedKind.CHECK]
    assert ai._kinds("[") == [] and ai._kinds('{"kind": "tooling"}') == []


def test_t64_only_the_ai_module_reaches_out() -> None:
    # 外へつなぐ部品と宛先は、app/services/ai.py にだけある。
    for path in [*Path("app").rglob("*.py"), *Path("tsunagu_rules").rglob("*.py")]:
        source = path.read_text()
        if path == Path("app/services/ai.py"):
            assert "urllib.request" in source and "api.anthropic.com" in source
            continue
        for word in ("urllib.request", "http.client", "anthropic", "httpx"):
            assert word not in source, f"{path}: {word}"
    # ai を読み込むのは、型の候補と、下書き（メッセージ・質問・まとめ）の入口だけ。
    # 候補探し・料金・見積・請求・明細・通知・台帳・規則は読み込まない。
    users = {
        str(path)
        for path in Path("app").rglob("*.py")
        if "services import ai" in path.read_text() or "services.ai" in path.read_text()
    }
    assert users == {
        "app/services/needs.py",
        "app/blueprints/needs.py",
        "app/blueprints/messages.py",
        "app/blueprints/guide.py",
        "app/blueprints/drafts.py",
    }
    for path in Path("tsunagu_rules").rglob("*.py"):
        assert (
            "import app" not in path.read_text() and "from app" not in path.read_text()
        )
    # 鍵とモデルの名前は、コードに書かない。
    source = Path("app/services/ai.py").read_text()
    assert "sk-" not in source and 'config["AI_MODEL"]' in source
    assert 'config["AI_API_KEY"]' in source


def test_brief_is_an_editable_masked_proposal_only(
    identity_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _owner(identity_app)
    path = "/o/1/needs/new"
    csrf = token(client, path)
    sent = _spy(monkeypatch, "入力の取り込み手順を整えたいです。")
    data = {
        "csrf_token": csrf,
        "action": "draft-brief",
        "body": BODY,
        "monthly_hours": "30",
        "weekdays": "0",
    }
    identity_app.config["AI_DRIVER"] = "off"
    assert client.post(path, data=data).status_code == 409
    assert not sent
    identity_app.config["AI_DRIVER"] = "fake"
    assert client.post("/o/2/needs/new", data=data).status_code == 404
    assert client.post(path, data={**data, "body": "申告書の作成"}).status_code == 422
    assert not sent
    page = client.post(path, data=data)
    assert page.status_code == 200
    assert "入力の取り込み手順を整えたいです。</textarea>" in page.get_data(
        as_text=True
    )
    assert len(sent) == 1 and "〈伏せ〉" in sent[0]
    assert all(secret not in sent[0] for secret in SECRETS)
    with identity_app.app_context():
        assert db.session.scalar(select(Need.id)) is None
    del data["action"]
    assert (
        client.post(
            path, data={**data, "body": "私が直した取り込みの相談です。"}
        ).status_code
        == 302
    )
    with identity_app.app_context():
        saved = db.session.scalar(select(Need))
        assert (
            saved
            and saved.status == NeedStatus.DRAFT
            and saved.body == "私が直した取り込みの相談です。"
        )


def test_brief_sends_masked_memo_to_claude_messages(identity_app: Flask) -> None:
    import json
    from io import BytesIO
    from unittest.mock import patch

    identity_app.config.update(
        AI_DRIVER="claude",
        AI_API_KEY="test-placeholder",
        AI_MODEL="fixture-model",
        AI_MONTHLY_LIMIT_YEN=1000,
        AI_INPUT_YEN_PER_MTOK=1,
        AI_OUTPUT_YEN_PER_MTOK=1,
    )
    client = _owner(identity_app)
    path = "/o/1/needs/new"
    form = {"csrf_token": token(client, path), "action": "draft-brief", "body": BODY}
    # HTTP の境界で架空の応答を返す。実サービスの鍵や通信は不要。
    payload = {
        "content": [{"type": "text", "text": "資料回収の手順を整理したいです。"}],
        "usage": {"input_tokens": 80, "output_tokens": 20},
    }
    with patch(
        "app.services.ai.urllib.request.urlopen",
        return_value=BytesIO(json.dumps(payload).encode()),
    ) as send:
        response = client.post(path, data=form)
    assert response.status_code == 200
    assert "資料回収の手順を整理したいです。</textarea>" in response.get_data(
        as_text=True
    )
    request = send.call_args.args[0]
    assert request.full_url == ai.ENDPOINT and request.method == "POST"
    body = json.loads(request.data)
    assert body["model"] == "fixture-model" and body["system"] == ai.BRIEF_SYSTEM
    memo = body["messages"][0]["content"]
    assert "〈伏せ〉" in memo and all(secret not in memo for secret in SECRETS)
    (run,) = _runs(identity_app)
    assert run.driver == "claude" and (run.input_tokens, run.output_tokens) == (80, 20)
