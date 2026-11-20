from datetime import timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import PASSWORD, _agree, sign_in, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    Engagement,
    MemberRole,
    Message,
    OfficeMember,
    OutboxMail,
    Role,
    User,
    utc_now,
)
from app.services.mail import mail_body
from app.services.messages import (
    DAILY_MAX,
    REMOVED_BODY,
    TEMPLATES,
    MessageConflict,
    send_message,
)
from app.services.tasks import tasks_for

TEXT = "架空の連絡です。\n次の訪問は、来週でお願いします。"


def _post(client: FlaskClient, path: str, action: str, body: str, **extra: str) -> int:
    data = {
        "csrf_token": token(client, "/account"),
        "action": action,
        "body": body,
        **extra,
    }
    return client.post(path + "/messages", data=data).status_code


def _messages(app: Flask) -> list[Message]:
    with app.app_context():
        return list(db.session.scalars(select(Message).order_by(Message.id)))


def test_t61_messages_stay_between_the_office_and_the_assigned_talent(
    database_app: Flask,
) -> None:
    app = database_app
    office, talent, office_path, talent_path = _agree(app)
    page = office.get(office_path + "/messages").get_data(as_text=True)
    assert "まだメッセージはありません" in page
    assert "顧問先の名前と金額は書かないでください" in page
    # 1画面に主のボタンは1つ。
    assert page.count('name="action" value=') == 1
    for key, (label, text) in TEMPLATES.items():
        assert f"?template={key}" in page and label in page
        chosen = office.get(f"{office_path}/messages?template={key}")
        assert f">{text}</textarea>" in chosen.get_data(as_text=True)
    unknown = office.get(office_path + "/messages?template=架空")
    assert "></textarea>" in unknown.get_data(as_text=True)

    # 本文は1〜400文字。確かめる段では、まだ送らない。
    for bad in ("", "   \n ", "あ" * 401):
        assert _post(office, office_path, "confirm", bad) == 422
        assert _post(office, office_path, "send", bad) == 422
    assert _post(office, office_path, "架空", TEXT) == 400
    data = {
        "csrf_token": token(office, "/account"),
        "action": "confirm",
        "body": f"  {TEXT}  ",
        "template": "schedule",
    }
    confirm = office.post(office_path + "/messages", data=data)
    html = confirm.get_data(as_text=True)
    assert confirm.status_code == 200 and "この文を送りますか" in html
    assert 'value="send">送る</button>' in html and 'value="edit">直す</button>' in html
    assert f'<a href="{office_path}/messages">やめる</a>' in html
    assert 'name="action" value="confirm"' not in html
    assert _messages(app) == []
    # 「直す」は、文を入力欄に戻すだけ。
    edit = office.post(office_path + "/messages", data={**data, "action": "edit"})
    assert f">  {TEXT}  </textarea>" in edit.get_data(as_text=True).replace("\r", "")
    assert _messages(app) == []

    # 送る人・どちら側か・伴走は、画面から受け取らない。
    forged = {
        "sender_user_id": "2",
        "from_office": "0",
        "engagement_id": "99",
        "read_at": "2001-01-01",
        "template": "schedule",
    }
    sent = office.post(
        office_path + "/messages", data={**data, **forged, "action": "send"}
    )
    assert sent.status_code == 302
    assert sent.headers["Location"] == office_path + "/messages?sent=1"
    (first,) = _messages(app)
    assert (first.sender_user_id, first.from_office, first.body) == (1, True, TEXT)
    assert (first.template, first.read_at) == ("schedule", None)
    # ひな形の名前は、知っているものだけ残す。
    assert _post(office, office_path, "send", "<b>架空</b>", template="架空") == 302
    assert _messages(app)[1].template is None

    # 送った側が開いても、既読にならない。相手が開くと、既読になる。
    mine = office.get(office_path + "/messages?sent=1").get_data(as_text=True)
    assert "data-letter-sent" in mine and "&lt;b&gt;架空&lt;/b&gt;" in mine
    assert mine.count("未読") == 2 and "新しいメッセージ" not in mine
    assert _messages(app)[0].read_at is None
    assert "メッセージ（未読 2通）" in talent.get(talent_path).get_data(as_text=True)
    theirs = talent.get(talent_path + "/messages").get_data(as_text=True)
    assert "新しいメッセージが2通あります" in theirs and "架空の連絡です。" in theirs
    assert all(row.read_at is not None for row in _messages(app))
    again = talent.get(talent_path + "/messages").get_data(as_text=True)
    assert "新しいメッセージ" not in again and "data-letter-sent" not in again
    assert "未読" not in talent.get(talent_path).get_data(as_text=True)

    # 届いたことは、翌朝のまとめに入る。メールに本文は入れない。
    with app.app_context():
        mail = db.session.scalar(
            select(OutboxMail).where(OutboxMail.recipient == "tsunagite@example.org")
        )
        assert mail is not None and mail.digest_key
        summary = mail_body(mail)
        assert "メッセージが届いています" in summary and "架空の連絡" not in summary

    # つなぎ手からも送れる。事務所の「今日やること」に出る。
    assert _post(talent, talent_path, "send", "架空の返事") == 302
    reply = _messages(app)[2]
    assert (reply.sender_user_id, reply.from_office, reply.read_at) == (2, False, None)
    with app.test_request_context():
        owner = db.session.get(User, 1)
        assert owner is not None
        assert "メッセージを読む" in [task.title for task in tasks_for(owner)]
    inbox = office.get("/account/messages").get_data(as_text=True)
    assert "架空候補0" in inbox and "<td data-numeric>1通</td>" in inbox
    assert f'href="{office_path}/messages"' in inbox

    # 運営は、読めない。伴走の画面にも、数と入口を出さない。
    admin = app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    assert admin.get(office_path + "/messages").status_code == 403
    assert admin.get(talent_path + "/messages").status_code == 403
    watched = admin.get(office_path).get_data(as_text=True)
    assert "/messages" not in watched and "未読" not in watched
    assert "送れる相手はいません" in admin.get("/account/messages").get_data(
        as_text=True
    )
    assert _messages(app)[2].read_at is None

    # 終わった伴走は、読むだけ。
    end = {"csrf_token": token(talent, talent_path), "reason": "goal_met"}
    assert talent.post(talent_path + "/end", data=end).status_code == 302
    for client, path in ((office, office_path), (talent, talent_path)):
        assert _post(client, path, "send", TEXT) == 409
        assert _post(client, path, "confirm", TEXT) == 409
        closed = client.get(path + "/messages").get_data(as_text=True)
        assert "新しいメッセージは送れません" in closed and "<textarea" not in closed
        assert "架空の返事" in closed
    # 画面を通らなくても、送る処理そのものが断る（終えるのと同時に送ったとき）。
    with app.test_request_context():
        ended = db.session.scalar(select(Engagement))
        assert ended is not None
        with pytest.raises(MessageConflict):
            send_message(ended, 1, TEXT, None, from_office=True)
    assert len(_messages(app)) == 3


def test_t61_other_members_and_limits(identity_app: Flask) -> None:
    app = identity_app
    office, talent, office_path, talent_path = _agree(app)
    with app.app_context():
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
        staff_id = staff.id
    # 職員も読めて、送れる。
    clerk = app.test_client()
    assert sign_in(clerk, "staff@example.org") == 302
    assert _post(clerk, office_path, "send", "架空の職員から") == 302
    assert _messages(app)[0].sender_user_id == staff_id
    shown = talent.get(talent_path + "/messages").get_data(as_text=True)
    assert "事務所・架空職員" in shown
    # 担当つなぎ手は、事務所の側の道からは入れない。事務所は、つなぎ手の側から入れない。
    assert talent.get(office_path + "/messages").status_code == 403
    assert office.get(talent_path + "/messages").status_code == 404
    assert _post(office, talent_path, "send", TEXT) == 404
    # 自治体には、相手がいない。
    town = app.test_client()
    assert sign_in(town, "municipality@example.org") == 302
    assert "送れる相手はいません" in town.get("/account/messages").get_data(
        as_text=True
    )
    assert town.get(office_path + "/messages").status_code == 404

    # 24時間で送れる数には上限がある（数えるのは、その人の、その伴走の分だけ）。
    with app.app_context():
        engagement_id = int(office_path.rsplit("/", 1)[-1])
        old = utc_now() - timedelta(hours=25)
        for index in range(DAILY_MAX):
            db.session.add(
                Message(
                    engagement_id=engagement_id,
                    sender_user_id=1,
                    from_office=True,
                    body="架空",
                    created_at=old if index == 0 else utc_now(),
                )
            )
        db.session.commit()
    assert _post(office, office_path, "send", TEXT) == 302
    assert _post(office, office_path, "send", TEXT) == 409
    assert _post(clerk, office_path, "send", TEXT) == 302
    assert _post(talent, talent_path, "send", TEXT) == 302


def test_t61_withdrawal_erases_what_the_person_wrote(identity_app: Flask) -> None:
    app = identity_app
    office, talent, office_path, talent_path = _agree(app)
    assert _post(office, office_path, "send", "架空の事務所から") == 302
    assert _post(talent, talent_path, "send", "架空のつなぎ手から") == 302
    with app.app_context():
        # 前にいた職員（今は使えないアカウント）が送った分。
        former = User(
            email="former@example.org",
            role=Role.OFFICE,
            display_name="架空の前の職員",
            is_active=False,
        )
        former.set_password(PASSWORD)
        db.session.add(former)
        db.session.flush()
        db.session.add(
            Message(
                engagement_id=int(office_path.rsplit("/", 1)[-1]),
                sender_user_id=former.id,
                from_office=True,
                body="架空の前の職員から",
            )
        )
        db.session.commit()
    end = {"csrf_token": token(talent, talent_path), "reason": "goal_met"}
    assert talent.post(talent_path + "/end", data=end).status_code == 302
    leave = {
        "csrf_token": token(talent, "/account"),
        "password": PASSWORD,
        "confirm": "yes",
    }
    assert talent.post("/account/withdraw", data=leave).status_code == 200
    # 退会した人が送った本文だけを消す。相手の分は残る。
    assert [row.body for row in _messages(app)] == [
        "架空の事務所から",
        REMOVED_BODY,
        "架空の前の職員から",
    ]
    page = office.get(office_path + "/messages").get_data(as_text=True)
    assert "架空のつなぎ手から" not in page and "退会した人" in page
    # ただ1人の代表が退会すると、事務所を閉じ、その伴走の本文をすべて消す。
    leave["csrf_token"] = token(office, "/account")
    assert office.post("/account/withdraw", data=leave).status_code == 200
    assert {row.body for row in _messages(app)} == {REMOVED_BODY}
