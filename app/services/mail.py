import base64
import hmac
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy import or_, select

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
    # Serialize a recipient's digest creation with their other notifications.
    with db.session.no_autoflush:
        account = db.session.get(
            User, user.id, with_for_update=True, populate_existing=True
        )
        if account is None or not account.is_active:
            return
        now = utc_now()
        # 00:00 UTC is 09:00 JST throughout the year.
        delivery = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        key = f"{account.id}-{delivery.date()}"
        mail = db.session.scalar(
            select(OutboxMail).where(OutboxMail.digest_key == key).with_for_update()
        )
    if mail is None:
        mail = next(
            (
                row
                for row in db.session.new
                if isinstance(row, OutboxMail) and row.digest_key == key
            ),
            None,
        )
    if mail is None:
        mail = OutboxMail(
            recipient=account.email,
            subject="つなぐだけ：今日やること",
            digest_key=key,
            deliver_after=delivery,
            is_demo=account.is_demo or current_app.config["APP_ENV"] != "production",
        )
        db.session.add(mail)
        summary = "ログインして「今日やること」をご確認ください。"
    else:
        summary = mail_body(mail)
    item = f"・{subject}：{body}"
    if item not in summary.splitlines():
        mail.body_encrypted = (
            _cipher().encrypt((summary + "\n" + item).encode()).decode("ascii")
        )


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
    for user in sorted(recipients, key=lambda person: person.id):
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
                or_(
                    OutboxMail.deliver_after.is_(None),
                    OutboxMail.deliver_after <= utc_now(),
                ),
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
