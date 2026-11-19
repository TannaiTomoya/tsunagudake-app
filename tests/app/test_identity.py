import re

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import (
    AuditLog,
    MemberRole,
    Municipality,
    MunicipalityMember,
    Office,
    OfficeMember,
    Role,
    User,
)

PASSWORD = "fixture passphrase only"


@pytest.fixture
def identity_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        for index, role in enumerate(Role, start=1):
            user = User(
                id=index,
                email=f"{role}@example.org",
                display_name="架空利用者",
                role=role,
            )
            user.set_password(PASSWORD)
            db.session.add(user)
        db.session.add_all(
            [
                Municipality(
                    id=1, code="90001", name="架空市", region_group="架空広域"
                ),
                Municipality(
                    id=2, code="90002", name="架空町", region_group="架空広域"
                ),
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                Office(id=1, name="架空事務所A", municipality_id=1),
                Office(id=2, name="架空事務所B", municipality_id=2),
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                OfficeMember(office_id=1, user_id=1, member_role=MemberRole.OWNER),
                MunicipalityMember(municipality_id=1, user_id=3),
            ]
        )
        db.session.commit()
    return app


def token(client: FlaskClient, path: str) -> str:
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]+value="([^"]+)"', html)
    assert match
    return match[1]


def sign_in(client: FlaskClient, email: str, password: str = PASSWORD) -> int:
    return client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": token(client, "/login"),
        },
    ).status_code


@pytest.mark.parametrize("path", ["/o/1/", "/t/", "/r/90001/"])
def test_t20_anonymous_redirect(identity_app: Flask, path: str) -> None:
    response = identity_app.test_client().get(path)
    assert response.status_code == 302 and response.headers["Location"].startswith(
        "/login?"
    )


@pytest.mark.parametrize(
    ("role", "path", "status"),
    [
        (Role.OFFICE, "/o/1/", 200),
        (Role.OFFICE, "/o/2/", 404),
        (Role.OFFICE, "/o/999/", 404),
        (Role.OFFICE, "/t/", 403),
        (Role.OFFICE, "/r/90001/", 403),
        (Role.TSUNAGITE, "/o/1/", 404),
        (Role.TSUNAGITE, "/t/", 200),
        (Role.MUNICIPALITY, "/o/1/", 404),
        (Role.MUNICIPALITY, "/r/90001/", 200),
        (Role.MUNICIPALITY, "/r/90002/", 404),
        (Role.MUNICIPALITY, "/r/99999/", 404),
        (Role.ADMIN, "/o/1/", 200),
    ],
)
def test_t21_role_and_membership(
    identity_app: Flask, role: Role, path: str, status: int
) -> None:
    client = identity_app.test_client()
    assert sign_in(client, f"{role}@example.org") == 302
    assert client.get(path).status_code == status
    if role == Role.ADMIN:
        with identity_app.app_context():
            row = db.session.scalar(
                select(AuditLog).where(AuditLog.action == "admin_viewed_office")
            )
            assert row and row.actor_user_id == 4 and row.office_id == 1


def test_password_change_revokes_session(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, " OFFICE@example.org ") == 302
    assert client.get("/o/1/").status_code == 200
    with identity_app.app_context():
        user = db.session.scalar(select(User).where(User.id == 1))
        assert user is not None
        user.set_password("updated fixture passphrase")
        db.session.commit()
    assert client.get("/o/1/").status_code == 302
    assert sign_in(client, "office@example.org") == 401


def test_login_audit_and_generic_failure(identity_app: Flask) -> None:
    client = identity_app.test_client()
    for email in ("unknown@example.org", "office@example.org"):
        response = client.post(
            "/login",
            data={
                "email": email,
                "password": "invalid",
                "csrf_token": token(client, "/login"),
            },
        )
        assert response.status_code == 401
        assert "メールアドレスまたはパスワードをご確認ください。" in response.get_data(
            as_text=True
        )
    assert sign_in(client, "office@example.org") == 302
    with identity_app.app_context():
        rows = list(db.session.scalars(select(AuditLog).order_by(AuditLog.id)))
        assert [row.action for row in rows] == ["login_failed", "login_failed", "login"]
        assert [row.actor_user_id for row in rows] == [None, None, 1]
        assert all(
            row.ip_hash and re.fullmatch(r"[0-9a-f]{64}", row.ip_hash) for row in rows
        )


def test_logout_requires_post_and_csrf(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    assert (
        client.post("/logout", data={"csrf_token": token(client, "/o/1/")}).status_code
        == 302
    )
    assert client.get("/o/1/").status_code == 302


def test_create_user_prompts_for_password(identity_app: Flask) -> None:
    result = identity_app.test_cli_runner().invoke(
        args=[
            "create-user",
            "--email",
            "new@example.org",
            "--display-name",
            "架空担当",
            "--role",
            "admin",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    assert PASSWORD not in result.output
    assert sign_in(identity_app.test_client(), "new@example.org") == 302


def _write_need(client: FlaskClient, path: str, body: str, category: str = "") -> str:
    response = client.post(
        path,
        data={
            "csrf_token": token(client, path),
            "body": body,
            "monthly_hours": "30",
            "weekdays": ["0", "2"],
            "manual_kind": category,
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    return response.headers["Location"]


def test_needs_demo_flow(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    assert "顧問先の名前と金額を書かないでください。" in client.get(
        "/o/1/needs/new"
    ).get_data(as_text=True)
    path = _write_need(client, "/o/1/needs/new", "資料が集まらない。過去と見比べる。")
    page = client.get(path).get_data(as_text=True)
    for text in ("資料回収", "チェックと比較", "集まらない", "120,000円", "水曜"):
        assert text in page
    assert (
        client.post(
            path + "/publish", data={"csrf_token": token(client, path)}
        ).status_code
        == 302
    )
    assert client.get(path + "/edit").status_code == 409
    with identity_app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.hourly_rate_yen = 5000
        db.session.commit()
    assert "120,000円" in client.get(path).get_data(as_text=True)
    assert (
        client.post(
            path + "/withdraw", data={"csrf_token": token(client, path)}
        ).status_code
        == 302
    )
    assert "取り下げ済み" in client.get(path).get_data(as_text=True)


def test_t25_block_precedes_manual_choice(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    path = _write_need(client, "/o/1/needs/new", "申告書の作成")
    _write_need(client, path + "/edit", "申告書の作成", "collect")
    assert "判断に使った言葉は「申告書の作成」" in client.get(path).get_data(
        as_text=True
    )
    csrf = token(client, path + "/edit")
    assert client.post(path + "/publish", data={"csrf_token": csrf}).status_code == 409
    _write_need(client, path + "/edit", "やり方に困っています", "collect")
    assert "手動で選択しました。" in client.get(path).get_data(as_text=True)
    assert client.post(path + "/publish", data={"csrf_token": csrf}).status_code == 302


def test_t21_t22_need_access(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    path = _write_need(client, "/o/1/needs/new", "資料が集まらない")
    csrf = token(client, path + "/edit")
    other = path.replace("/o/1/", "/o/2/", 1)
    assert client.get(other).status_code == 404
    assert client.post(other + "/publish", data={"csrf_token": csrf}).status_code == 404
    with identity_app.app_context():
        member = db.session.scalar(select(OfficeMember))
        assert member is not None
        member.member_role = MemberRole.STAFF
        db.session.commit()
    assert client.get(path).status_code == 200
    assert client.post(path + "/publish", data={"csrf_token": csrf}).status_code == 403
    assert client.post(path + "/withdraw", data={"csrf_token": csrf}).status_code == 403
    admin = identity_app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    assert admin.get(path).status_code == 200
    assert admin.get(path + "/edit").status_code == 403
