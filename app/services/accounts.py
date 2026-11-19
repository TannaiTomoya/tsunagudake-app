from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe

from email_validator import EmailNotValidError, validate_email
from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import record_audit
from app.extensions import db
from app.models import (
    Invitation,
    MemberRole,
    Municipality,
    MunicipalityMember,
    Office,
    OfficeMember,
    PasswordResetToken,
    Role,
    User,
    utc_now,
)
from app.ratelimit import check_login, finish_login, login_buckets
from app.services.mail import enqueue


class AccountError(Exception):
    pass


@dataclass(frozen=True)
class Invite:
    email: str
    role: Role
    office_id: int | None
    member_role: MemberRole | None
    municipality_id: int | None


def _hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def _link(path: str, token: str) -> str:
    return f"{current_app.config['BASE_URL'].rstrip('/')}/{path}#{token}"


def _email(raw: str) -> str:
    try:
        email = validate_email(
            raw.strip(), check_deliverability=False
        ).normalized.lower()
    except EmailNotValidError:
        raise AccountError("メールアドレスを確認してください。") from None
    if len(email) > 255:
        raise AccountError("メールアドレスが長すぎます。")
    return email


def issue_invitation(data: Invite, actor_id: int) -> None:
    address = _email(data.email)
    if db.session.scalar(select(User.id).where(User.email == address)) is not None:
        raise AccountError("すでに登録されているメールアドレスです。")
    demo = current_app.config["APP_ENV"] == "demo"
    office_id = municipality_id = None
    member_role = None
    if data.role == Role.OFFICE:
        office = db.session.get(Office, data.office_id) if data.office_id else None
        if (
            office is None
            or not office.is_active
            or office.is_demo != demo
            or data.member_role is None
        ):
            raise AccountError("招待する事務所と所属区分を選んでください。")
        office_id, member_role = office.id, data.member_role
    elif data.role == Role.MUNICIPALITY:
        town = (
            db.session.get(Municipality, data.municipality_id)
            if data.municipality_id
            else None
        )
        if town is None:
            raise AccountError("担当する市町村を選んでください。")
        municipality_id = town.id
    elif data.role != Role.TSUNAGITE:
        raise AccountError("招待できる立場を選んでください。")

    token = token_urlsafe(32)
    expiry = utc_now() + timedelta(hours=48)
    db.session.add(
        Invitation(
            email=address,
            role=data.role,
            office_id=office_id,
            member_role=member_role,
            municipality_id=municipality_id,
            token_hash=_hash(token),
            expires_at=expiry,
            is_demo=demo,
            created_by=actor_id,
        )
    )
    enqueue(
        address,
        "つなぐだけへの招待",
        "48時間以内に、次のリンクで登録してください。\n"
        + _link("accept-invite", token),
        demo=demo,
        expires_at=expiry,
    )
    record_audit("account_invited", actor_id=actor_id, office_id=office_id)
    db.session.commit()


def accept_invitation(token: str, display_name: str, password: str) -> None:
    invite = db.session.scalar(
        select(Invitation)
        .where(Invitation.token_hash == _hash(token))
        .with_for_update()
    )
    now = utc_now()
    if (
        invite is None
        or invite.used_at is not None
        or invite.expires_at <= now
        or invite.is_demo != (current_app.config["APP_ENV"] == "demo")
    ):
        raise AccountError("このリンクは無効か、有効期限が切れています。")
    user = User(
        email=invite.email,
        display_name=display_name,
        role=invite.role,
        is_demo=invite.is_demo,
    )
    user.set_password(password)
    db.session.add(user)
    invite.used_at = now
    try:
        db.session.flush()
        if invite.office_id and invite.member_role:
            office = db.session.get(Office, invite.office_id, with_for_update=True)
            if (
                office is None
                or not office.is_active
                or office.is_demo != invite.is_demo
            ):
                db.session.rollback()
                raise AccountError(
                    "招待先の事務所を確認できません。運営にご確認ください。"
                )
            db.session.add(
                OfficeMember(
                    user_id=user.id,
                    office_id=office.id,
                    member_role=invite.member_role,
                )
            )
        if invite.municipality_id:
            db.session.add(
                MunicipalityMember(
                    user_id=user.id, municipality_id=invite.municipality_id
                )
            )
        record_audit(
            "invitation_accepted", actor_id=user.id, office_id=invite.office_id
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise AccountError(
            "このリンクで登録できません。運営にご確認ください。"
        ) from None


def request_reset(email: str) -> None:
    address = email.strip().lower()
    buckets = login_buckets(address, namespace="reset")
    check_login(buckets)
    user = db.session.scalar(select(User).where(User.email == address))
    if (
        user is not None
        and user.email == address
        and user.is_active
        and not user.is_demo
    ):
        token = token_urlsafe(32)
        expiry = utc_now() + timedelta(minutes=30)
        db.session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash(token),
                session_nonce=user.session_nonce,
                expires_at=expiry,
            )
        )
        enqueue(
            address,
            "つなぐだけのパスワード再設定",
            "30分以内に、次のリンクで再設定してください。\n"
            + _link("reset-password", token),
            demo=False,
            expires_at=expiry,
        )
    finish_login(buckets, successful=False)
    db.session.commit()


def reset_password(token: str, password: str) -> None:
    key = _hash(token)
    user_id = db.session.scalar(
        select(PasswordResetToken.user_id).where(PasswordResetToken.token_hash == key)
    )
    user = (
        db.session.get(User, user_id, with_for_update=True, populate_existing=True)
        if user_id
        else None
    )
    reset = db.session.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == key,
        )
        .with_for_update()
    )
    now = utc_now()
    if (
        user is None
        or not user.is_active
        or user.is_demo
        or reset is None
        or reset.used_at is not None
        or reset.expires_at <= now
        or reset.session_nonce != user.session_nonce
    ):
        raise AccountError("このリンクは無効か、有効期限が切れています。")
    user.set_password(password)
    reset.used_at = now
    record_audit("password_reset", actor_id=user.id)
    db.session.commit()
