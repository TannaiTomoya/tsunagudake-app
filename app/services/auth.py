import hmac
from typing import cast

from flask import current_app, session
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app.audit import record_audit
from app.extensions import db, login_manager
from app.models import User, utc_now
from app.ratelimit import check_login, finish_login, login_buckets

_DUMMY_HASH = generate_password_hash("unused account")


def requires_second_factor(user: User) -> bool:
    return not user.is_demo and (
        current_app.config["APP_ENV"] in {"production", "demo"}
        or current_app.config.get("MFA_REQUIRED", False)
    )


def _can_sign_in(user: User) -> bool:
    return user.is_active and (
        not user.is_demo or current_app.config["APP_ENV"] in {"demo", "development"}
    )


@login_manager.user_loader  # type: ignore[untyped-decorator]
def load_user(identity: str) -> User | None:
    user_id, _, nonce = identity.partition(":")
    if not user_id.isascii() or not user_id.isdecimal() or len(user_id) > 10:
        return None
    user = db.session.get(User, int(user_id))
    if user and _can_sign_in(user) and hmac.compare_digest(user.session_nonce, nonce):
        if (
            requires_second_factor(user)
            and session.get("second_factor_identity") != identity
        ):
            return None
        return user
    return None


def authenticate(email: str, password: str, *, demo_only: bool = False) -> User | None:
    email = email.strip().lower()
    buckets = login_buckets(email)
    check_login(buckets)
    user = db.session.scalar(select(User).where(User.email == email))
    if user and user.email != email:
        user = None
    if demo_only and user and not user.is_demo:
        user = None
    password_valid = check_password_hash(
        user.password_hash if user else _DUMMY_HASH, password
    )
    if user is None or not _can_sign_in(user) or not password_valid:
        finish_login(buckets, successful=False)
        record_audit("login_failed")
        db.session.commit()
        return None

    finish_login(buckets, successful=True)
    if requires_second_factor(user):
        record_audit("password_verified", actor_id=user.id)
    else:
        user.last_login_at = utc_now()
        record_audit("login", actor_id=user.id)
    db.session.commit()
    return cast(User, user)
