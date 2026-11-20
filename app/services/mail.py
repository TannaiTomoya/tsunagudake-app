import base64
import hmac
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import MemberRole, OfficeMember, OutboxMail, User, utc_now


class MailError(Exception):
    pass


def _cipher() -> Fernet:
    key = current_app.secret_key
    assert key
    if isinstance(key, str):
        key = key.encode()
    digest = hmac.digest(key, b"tsunagudake.outbox.v1", "sha256")
    return Fernet(base64.urlsafe_b64encode(digest))


def mail_body(mail: OutboxMail) -> str:
    if mail.body_encrypted is None:
        return ""
    try:
        return _cipher().decrypt(mail.body_encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken, UnicodeError:
        raise MailError("メール本文を読み取れません。") from None


def enqueue(
    recipient: str,
    subject: str,
    body: str,
    *,
    demo: bool,
    expires_at: datetime | None = None,
) -> None:
    db.session.add(
        OutboxMail(
            recipient=recipient,
            subject=subject,
            body_encrypted=_cipher().encrypt(body.encode()).decode("ascii"),
            is_demo=demo or current_app.config["APP_ENV"] != "production",
            expires_at=expires_at,
        )
    )


def notify_user(user: User, subject: str, body: str) -> None:
    if user.is_active:
        enqueue(user.email, subject, body, demo=user.is_demo)


def notify_office(
    office_id: int, subject: str, body: str, *, owners_only: bool = False
) -> None:
    members = (
        select(User)
        .join(OfficeMember)
        .where(
            OfficeMember.office_id == office_id,
            User.is_active.is_(True),
        )
    )
    if owners_only:
        members = members.where(OfficeMember.member_role == MemberRole.OWNER)
    with db.session.no_autoflush:
        recipients = db.session.scalars(members).all()
    for user in recipients:
        notify_user(user, subject, body)


def _deliver(mail: OutboxMail) -> None:
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    if not all((host, sender, username, password)):
        raise MailError("SMTP の送信設定を確認してください。")
    assert host and sender and username and password
    try:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = mail.recipient
        message["Subject"] = mail.subject
        message.set_content(mail_body(mail))
        port = int(os.environ.get("SMTP_PORT", "465"))
        with smtplib.SMTP_SSL(
            host, port, context=ssl.create_default_context(), timeout=10
        ) as relay:
            relay.login(username, password)
            relay.send_message(message)
    except OSError, ValueError, smtplib.SMTPException:
        raise MailError(
            "SMTP への送信に失敗しました。未送信の記録を保持します。"
        ) from None


def send_pending(limit: int = 20) -> int:
    if current_app.config["APP_ENV"] != "production":
        return 0
    sent = 0
    for _ in range(limit):
        mail = db.session.scalar(
            select(OutboxMail)
            .where(
                OutboxMail.is_demo.is_(False),
                OutboxMail.sent_at.is_(None),
                OutboxMail.body_encrypted.is_not(None),
            )
            .order_by(OutboxMail.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if mail is None:
            db.session.rollback()
            break
        now = utc_now()
        if mail.expires_at is None or mail.expires_at > now:
            try:
                _deliver(mail)
            except MailError:
                db.session.rollback()
                raise
            mail.sent_at = utc_now()
            sent += 1
        mail.body_encrypted = None
        db.session.commit()
    return sent
