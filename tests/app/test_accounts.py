from datetime import timedelta
from hashlib import sha256
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import (
    _agree,
    sign_in,
    token,
)  # noqa: F401
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    Invitation,
    MemberRole,
    OfficeMember,
    OutboxMail,
    PasswordResetToken,
    Role,
    User,
    today_in_japan,
    utc_now,
)
from app.services.mail import enqueue, mail_body

NEW_PASSWORD = "fixture updated passphrase"


def _mailed_token(app: Flask, email: str) -> str:
    with app.app_context():
        mail = db.session.scalar(
            select(OutboxMail)
            .where(OutboxMail.recipient == email)
            .order_by(OutboxMail.id.desc())
            .limit(1)
        )
        assert mail and mail.body_encrypted
        raw = mail_body(mail).rsplit("#", 1)[1]
        assert raw not in mail.body_encrypted
        return raw


def _set_password(client: FlaskClient, path: str, raw: str) -> int:
    return client.post(
        path,
        data={
            "csrf_token": token(client, path),
            "token": raw,
            "password": NEW_PASSWORD,
            "confirmation": NEW_PASSWORD,
            "display_name": "招待された架空職員",
            "role": "admin",
            "office": "2",
        },
    ).status_code


def test_t36_invitation_is_single_use_and_scoped(database_app: Flask) -> None:
    admin = database_app.test_client()
    invite = "/admin/invitations/new"
    visitor = database_app.test_client()
    assert sign_in(visitor, "office@example.org") == 302
    assert visitor.get(invite).status_code == 404
    assert visitor.get("/admin/mail").status_code == 404
    assert sign_in(admin, "admin@example.org") == 302
    payload = {
        "csrf_token": token(admin, invite),
        "email": "new@example.org",
        "role": "office",
        "office": "1",
        "member_role": "staff",
        "municipality": "0",
    }
    assert admin.post(invite, data=payload).status_code == 302
    raw = _mailed_token(database_app, "new@example.org")
    assert raw in admin.get("/admin/mail").get_data(as_text=True)
    with database_app.app_context():
        row = db.session.scalar(select(Invitation))
        assert row and row.token_hash == sha256(raw.encode()).hexdigest()
    assert _set_password(visitor, "/accept-invite", raw) == 302
    assert _set_password(visitor, "/accept-invite", raw) == 422
    with database_app.app_context():
        user = db.session.scalar(select(User).where(User.email == "new@example.org"))
        assert user and user.role == Role.OFFICE
        member = db.session.scalar(
            select(OfficeMember).where(OfficeMember.user_id == user.id)
        )
        assert (
            member and member.office_id == 1 and member.member_role == MemberRole.STAFF
        )

    address = "expired@example.org"
    assert admin.post(invite, data={**payload, "email": address}).status_code == 302
    expired = _mailed_token(database_app, address)
    with database_app.app_context():
        invitation = db.session.scalar(
            select(Invitation).where(Invitation.email == address)
        )
        assert invitation
        invitation.expires_at = utc_now() - timedelta(seconds=1)
        db.session.commit()
    assert _set_password(visitor, "/accept-invite", expired) == 422


def test_t37_reset_is_private_single_use_and_revokes_sessions(
    database_app: Flask,
) -> None:
    active = database_app.test_client()
    assert sign_in(active, "office@example.org") == 302
    client = database_app.test_client()
    request_path = "/forgot-password"
    csrf = token(client, request_path)
    known = client.post(
        request_path, data={"email": "office@example.org", "csrf_token": csrf}
    )
    unknown = client.post(
        request_path, data={"email": "absent@example.org", "csrf_token": csrf}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.data == unknown.data

    expired = _mailed_token(database_app, "office@example.org")
    with database_app.app_context():
        row = db.session.scalar(select(PasswordResetToken))
        assert row
        row.expires_at = utc_now() - timedelta(seconds=1)
        db.session.commit()
    assert _set_password(client, "/reset-password", expired) == 422
    for _ in range(2):
        assert (
            client.post(
                request_path, data={"email": "office@example.org", "csrf_token": csrf}
            ).status_code
            == 200
        )
        raw = _mailed_token(database_app, "office@example.org")
        if _ == 0:
            older = raw
    assert _set_password(client, "/reset-password", raw) == 302
    assert _set_password(client, "/reset-password", raw) == 422
    assert _set_password(client, "/reset-password", older) == 422
    assert active.get("/o/1/").status_code == 302
    assert sign_in(active, "office@example.org", NEW_PASSWORD) == 302


def test_t38_events_are_queued_and_demo_never_sends(identity_app: Flask) -> None:
    office, talent, office_path, talent_path = _agree(identity_app)
    visit_path = talent_path + "/visits/new"
    day = today_in_japan()
    visit = {
        "csrf_token": token(talent, visit_path),
        "day": day.isoformat(),
        "minutes": "60",
        "category": "collect",
    }
    assert talent.post(visit_path, data=visit).status_code == 302
    assert talent.post(visit_path, data=visit).status_code == 409
    assert (
        office.post(
            office_path + "/reductions",
            data={
                "csrf_token": token(office, office_path),
                "month": day.strftime("%Y-%m"),
                "category": "collect",
                "before": "30",
                "after": "10",
            },
        ).status_code
        == 302
    )
    with identity_app.app_context():
        mails = db.session.scalars(select(OutboxMail).order_by(OutboxMail.id)).all()
        assert [row.subject for row in mails] == [
            "伴走の声かけが届きました",
            "伴走が決まりました",
            "伴走が決まりました",
            "訪問の確認をお願いします",
            "減った時間の確認をお願いします",
        ]
        assert all(row.is_demo and row.sent_at is None for row in mails)
    identity_app.config["APP_ENV"] = "demo"
    with patch("app.services.mail.smtplib.SMTP_SSL") as connect:
        result = identity_app.test_cli_runner().invoke(args=["send-mail"])
        assert result.exit_code == 0
        connect.assert_not_called()


def test_t38_smtp_success_discards_body_and_failure_retries(
    database_app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_app.config["APP_ENV"] = "production"
    for name, value in (
        ("HOST", "smtp.example.org"),
        ("FROM", "notices@example.org"),
        ("USERNAME", "fixture"),
        ("PASSWORD", "fixture"),
        ("PORT", "465"),
    ):
        monkeypatch.setenv(f"SMTP_{name}", value)
    with database_app.app_context():
        enqueue("fixture@example.org", "架空のお知らせ", "架空の本文", demo=False)
        db.session.commit()
    runner = database_app.test_cli_runner()
    with patch("app.services.mail.smtplib.SMTP_SSL", side_effect=OSError("fixture")):
        assert runner.invoke(args=["send-mail"]).exit_code == 1
    with database_app.app_context():
        mail = db.session.scalar(select(OutboxMail))
        assert mail and mail.sent_at is None and mail.body_encrypted
    with patch("app.services.mail.smtplib.SMTP_SSL") as connect:
        assert runner.invoke(args=["send-mail"]).exit_code == 0
        relay = connect.return_value.__enter__.return_value
        message = relay.send_message.call_args.args[0]
        assert message["To"] == "fixture@example.org"
        relay.login.assert_called_once()
    with database_app.app_context():
        mail = db.session.scalar(select(OutboxMail))
        assert mail and mail.sent_at and mail.body_encrypted is None
