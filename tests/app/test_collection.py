import re

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select
from test_identity import PASSWORD, sign_in, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    AuditLog,
    CollectionItem,
    MemberRole,
    Office,
    OfficeMember,
    Role,
    User,
    today_in_japan,
)
from app.services.collection import shift_month

BOARD = "/o/1/collection"
API = "/api/offices/1/collection-counts"


def _month() -> str:
    return today_in_japan().strftime("%Y-%m")


def _post(client: FlaskClient, action: str, client_no: str = "", **extra: str) -> int:
    data = {
        "csrf_token": token(client, BOARD),
        "month": _month(),
        "action": action,
        "client_no": client_no,
        **extra,
    }
    return client.post(BOARD, data=data).status_code


def _items(app: Flask, month: str | None = None) -> list[tuple[int, bool, int]]:
    with app.app_context():
        return [
            (item.client_no, item.is_ready, item.received_count)
            for item in db.session.scalars(
                select(CollectionItem)
                .where(CollectionItem.month == (month or _month()))
                .order_by(CollectionItem.client_no)
            )
        ]


def _issue(client: FlaskClient) -> str:
    page = client.post(
        BOARD + "/token", data={"csrf_token": token(client, BOARD)}
    ).get_data(as_text=True)
    found = re.search(r'<code class="token-once">([^<]+)</code>', page)
    assert found
    return found[1]


def _send(
    app: Flask, secret: str | None, payload: object, path: str = API
) -> tuple[int, dict[str, object]]:
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    response = app.test_client().post(path, json=payload, headers=headers)
    return response.status_code, response.get_json(silent=True) or {}


def test_t56_board_holds_numbers_only(database_app: Flask) -> None:
    app = database_app
    owner = app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    page = owner.get(BOARD).get_data(as_text=True)
    assert "この月の番号は、まだありません" in page
    assert "顧問先の名前と金額は書かないでください" in page
    # 番号だけを受け取る。名前・範囲の外・小数は 422 で、何も残らない。
    for bad in ("架空商店", "0", "10000", "1.5", "-3", ""):
        assert _post(owner, "add", bad) == 422
    assert _post(owner, "rename", "3") == 422
    assert _items(app) == []
    # 範囲の外は、DB の制約に頼らず、理由を書いて返す。
    zero = owner.post(
        BOARD,
        data={
            "csrf_token": token(owner, BOARD),
            "month": _month(),
            "action": "add",
            "client_no": "0",
        },
    )
    assert zero.status_code == 422
    assert "1〜9999の整数で入力してください" in zero.get_data(as_text=True)
    assert _post(owner, "add", "12") == 302
    assert _post(owner, "add", "３") == 302
    assert _post(owner, "add", "12") == 302
    assert _items(app) == [(3, False, 0), (12, False, 0)]
    # 件数・状態・事務所を、画面から直に送っても使わない。
    assert (
        _post(
            owner,
            "ready",
            "12",
            received_count="99",
            is_ready="0",
            office_id="2",
            note="架空商店 120,000円",
        )
        == 302
    )
    assert _items(app) == [(3, False, 0), (12, True, 0)]
    page = owner.get(BOARD).get_data(as_text=True)
    assert "そろった 1／2。届いた資料 0件。" in page
    assert "架空商店" not in page and "120,000" not in page
    # 主のボタンは「番号を足す」だけ。行ごとの操作は副のボタン。
    assert '<button type="submit" name="action" value="add">番号を足す</button>' in page
    assert (
        page.count('<button class="secondary outline" type="submit" name="action"') == 2
    )
    assert _post(owner, "pending", "12") == 302
    assert _items(app) == [(3, False, 0), (12, False, 0)]
    with app.app_context():
        # 事務所2のものは混ざらない。
        assert db.session.scalars(select(CollectionItem.office_id)).unique().all() == [
            1
        ]
        assert db.session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "collection_item_saved")
        )

    # 先月の番号を引き継ぐ（そろった・件数は引き継がない）。
    following = shift_month(_month(), 1)
    assert "先月の番号を引き継ぐ" in owner.get(f"{BOARD}?month={following}").get_data(
        as_text=True
    )
    data = {"csrf_token": token(owner, BOARD), "month": following, "action": "carry"}
    assert owner.post(BOARD, data=data).status_code == 302
    assert owner.post(BOARD, data=data).status_code == 422
    assert _items(app, following) == [(3, False, 0), (12, False, 0)]
    # 来月より先・形の違う月は開けない。
    for month in (shift_month(_month(), 2), "2019-12", "2030-13", "x"):
        assert owner.get(f"{BOARD}?month={month}").status_code == 404
        assert owner.post(BOARD, data={**data, "month": month}).status_code == 422


def test_t56_who_can_use_the_board(identity_app: Flask) -> None:
    app = identity_app
    with app.app_context():
        for email, office_id, role in (
            ("staff@example.org", 1, MemberRole.STAFF),
            ("outside@example.org", 2, MemberRole.OWNER),
        ):
            person = User(email=email, role=Role.OFFICE, display_name="架空利用者")
            person.set_password(PASSWORD)
            db.session.add(person)
            db.session.flush()
            db.session.add(
                OfficeMember(user_id=person.id, office_id=office_id, member_role=role)
            )
        db.session.commit()
    staff, outside, admin = (app.test_client() for _ in range(3))
    assert sign_in(staff, "staff@example.org") == 302
    assert sign_in(outside, "outside@example.org") == 302
    assert sign_in(admin, "admin@example.org") == 302
    # 職員は番号を足せるが、合言葉は発行できない。
    assert _post(staff, "add", "7") == 302
    page = staff.get(BOARD).get_data(as_text=True)
    assert "合言葉を発行できるのは、事務所の代表だけです" in page
    assert (
        staff.post(
            BOARD + "/token", data={"csrf_token": token(staff, BOARD)}
        ).status_code
        == 403
    )
    # 他の事務所の人には、ボードが無いのと同じ。自分の事務所には番号が出ない。
    assert outside.get(BOARD).status_code == 404
    assert _post_as(outside, BOARD) == 404
    assert "この月の番号は、まだありません" in outside.get("/o/2/collection").get_data(
        as_text=True
    )
    # 運営は見るだけ。
    page = admin.get(BOARD).get_data(as_text=True)
    assert "運営は見るだけです" in page and 'name="client_no"' not in page
    assert _post_as(admin, BOARD) == 403
    assert _items(app) == [(7, False, 0)]
    with app.app_context():
        hashes = db.session.scalars(select(Office.collection_token_hash)).all()
        assert hashes == [None, None]


def _post_as(client: FlaskClient, path: str) -> int:
    data = {
        "csrf_token": token(client, "/login"),
        "month": _month(),
        "action": "add",
        "client_no": "9",
    }
    return client.post(path, data=data).status_code


def test_t57_counts_arrive_with_a_secret_and_nothing_else(database_app: Flask) -> None:
    app = database_app
    owner = app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    assert _post(owner, "ready", "3") == 302
    month = _month()
    good = {"month": month, "counts": [{"client_no": 3, "received": 12}]}
    # 合言葉を発行する前は、誰も送れない。
    assert _send(app, None, good)[0] == 401
    assert _send(app, "tsg_" + "a" * 43, good)[0] == 401
    secret = _issue(owner)
    assert secret.startswith("tsg_") and len(secret) > 40
    with app.app_context():
        stored = db.session.scalar(
            select(Office.collection_token_hash).where(Office.id == 1)
        )
        # DB に残すのはハッシュだけ。
        assert stored is not None and len(stored) == 64 and secret not in stored
    # 合言葉は1回だけ表示する。
    assert secret not in owner.get(BOARD).get_data(as_text=True)

    status, body = _send(app, secret, good)
    assert (status, body) == (200, {"saved": 1})
    # 件数を受け取っても、「そろった」は人が決めたまま。
    assert _items(app) == [(3, True, 12)]
    # その月の合計なので、同じ値をもう一度送っても変わらない。
    # 知らない番号は「まだ」で足す。
    more = {
        "month": month,
        "counts": [*good["counts"], {"client_no": 8, "received": 0}],
    }
    assert _send(app, secret, more) == (200, {"saved": 2})
    assert _items(app) == [(3, True, 12), (8, False, 0)]

    # 名前・金額などのキーが1つでもあれば、何も保存しない。
    for bad in (
        {**good, "client_name": "架空商店"},
        {
            "month": month,
            "counts": [{"client_no": 3, "received": 99, "amount": 120000}],
        },
        {"month": month, "counts": [{"client_no": 3, "received": 99, "name": "架空"}]},
        {"month": month, "counts": [{"client_no": "3", "received": 99}]},
        {"month": month, "counts": [{"client_no": 3, "received": True}]},
        {"month": month, "counts": [{"client_no": 3, "received": 10000}]},
        {"month": month, "counts": [{"client_no": 0, "received": 1}]},
        {"month": month, "counts": [{"client_no": 3, "received": 1}] * 2},
        {"month": month, "counts": []},
        {"month": month, "counts": [{"client_no": 1, "received": 1}] * 501},
        {"month": "2030-13", "counts": good["counts"]},
        {"month": shift_month(month, 2), "counts": good["counts"]},
        {"counts": good["counts"]},
        [good],
        "架空商店",
    ):
        assert _send(app, secret, bad)[0] == 422, bad
    assert (
        app.test_client()
        .post(API, data="month=x", headers={"Authorization": f"Bearer {secret}"})
        .status_code
        == 422
    )
    assert _items(app) == [(3, True, 12), (8, False, 0)]

    # 合言葉は、その事務所にだけ効く。無い事務所も同じ 401。
    for path in (
        "/api/offices/2/collection-counts",
        "/api/offices/999/collection-counts",
    ):
        status, body = _send(app, secret, good, path)
        assert (status, body) == (401, {"error": "unauthorized"})
    # ログイン中の Cookie では使えない。
    assert owner.post(API, json=good).status_code == 401
    # 発行し直すと、前の合言葉は使えない。
    renewed = _issue(owner)
    assert renewed != secret
    assert _send(app, secret, good)[0] == 401
    assert _send(app, renewed, good)[0] == 200
    # 閉じた事務所は受け取らない。
    with app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.is_active = False
        db.session.commit()
    assert _send(app, renewed, good)[0] == 401


def test_t57_wrong_secrets_are_rate_limited_per_address(identity_app: Flask) -> None:
    app = identity_app
    owner = app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    secret = _issue(owner)
    good = {"month": _month(), "counts": [{"client_no": 3, "received": 1}]}
    limit = app.config["RATE_LOGIN_IP_MAX"]
    for _ in range(limit):
        assert _send(app, "tsg_wrong", good)[0] == 401
    assert _send(app, "tsg_wrong", good)[0] == 429
    assert _send(app, secret, good)[0] == 429
    assert _items(app) == []
    with app.app_context():
        # 合言葉は、監査記録にも残さない。
        actions = db.session.scalars(select(AuditLog.action)).all()
        assert "collection_token_issued" in actions


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_t57_api_accepts_post_only(identity_app: Flask, method: str) -> None:
    assert getattr(identity_app.test_client(), method)(API).status_code == 405
