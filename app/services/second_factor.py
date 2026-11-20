"""Password-verified challenges and one-time credentials held on the server."""

import base64
import hashlib
import hmac
import secrets
from time import time

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from flask import current_app, session

from app.audit import record_audit
from app.extensions import db
from app.models import SecondFactor, User, utc_now
from app.ratelimit import check_login, finish_login, login_buckets


def _cipher() -> Fernet:
    key = current_app.secret_key
    assert key
    if isinstance(key, str):
        key = key.encode()
    return Fernet(
        base64.urlsafe_b64encode(hmac.digest(key, b"tsunagudake.totp.v1", "sha256"))
    )


def begin(user: User) -> None:
    identity = user.get_id()
    locked = db.session.get(User, user.id, with_for_update=True, populate_existing=True)
    if locked is None or not locked.is_active or locked.get_id() != identity:
        return
    factor = db.session.get(SecondFactor, user.id, with_for_update=True)
    if factor is None:
        factor = SecondFactor(user_id=user.id)
        db.session.add(factor)
    if factor.last_step is None:
        factor.secret_cipher = (
            _cipher().encrypt(secrets.token_bytes(20)).decode("ascii")
        )
        factor.enrollment_ticket = secrets.token_hex(16)
        session["enrollment_ticket"] = factor.enrollment_ticket
    session["pending_identity"] = identity
    session["pending_until"] = int(time()) + 300
    db.session.commit()


def pending(*, submitted: bool) -> tuple[User | None, SecondFactor | None]:
    identity = session.get("pending_identity", "")
    user_id, _, _nonce = identity.partition(":")
    if not user_id.isdecimal() or session.get("pending_until", 0) <= time():
        return None, None
    if submitted:
        check_login(login_buckets(user_id, namespace="mfa"))
    user = db.session.get(User, int(user_id), with_for_update=submitted)
    if not user or not user.is_active or user.get_id() != identity or user.is_demo:
        return None, None
    factor = db.session.get(SecondFactor, user.id, with_for_update=submitted)
    if not factor or (
        factor.last_step is None
        and factor.enrollment_ticket != session.get("enrollment_ticket")
    ):
        return None, None
    return user, factor


def setup_key(factor: SecondFactor) -> str | None:
    if factor.last_step is not None:
        return None
    return base64.b32encode(_cipher().decrypt(factor.secret_cipher.encode())).decode(
        "ascii"
    )


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def confirm(user: User, factor: SecondFactor, raw: str) -> tuple[bool, list[str]]:
    code = raw.strip().replace("-", "").upper()
    recovery: list[str] = []
    step = int(time()) // 30
    accepted = False
    if len(code) == 6 and code.isascii() and code.isdigit():
        totp = TOTP(
            _cipher().decrypt(factor.secret_cipher.encode()), 6, hashes.SHA1(), 30
        )
        for offset in (0, -1, 1):
            candidate = step + offset
            if (
                factor.last_step is None or candidate > factor.last_step
            ) and hmac.compare_digest(totp.generate(candidate * 30), code.encode()):
                if factor.last_step is None:
                    factor.enrollment_ticket = None
                    recovery = [secrets.token_hex(8).upper() for _ in range(8)]
                    factor.recovery_hashes = [_digest(item) for item in recovery]
                factor.last_step = candidate
                accepted = True
                break
    elif len(code) == 16 and code.isascii() and factor.last_step is not None:
        digest = _digest(code)
        if any(hmac.compare_digest(item, digest) for item in factor.recovery_hashes):
            factor.recovery_hashes = [
                item for item in factor.recovery_hashes if item != digest
            ]
            accepted = True
    finish_login(login_buckets(str(user.id), namespace="mfa"), successful=accepted)
    if accepted:
        user.last_login_at = utc_now()
        record_audit("login", actor_id=user.id)
    else:
        record_audit("second_factor_failed", actor_id=user.id)
    db.session.commit()
    return accepted, recovery
