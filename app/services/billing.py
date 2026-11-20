import base64
import hmac

from cryptography.fernet import Fernet
from flask import current_app


def _cipher() -> Fernet:
    key = current_app.secret_key
    assert key
    if isinstance(key, str):
        key = key.encode()
    return Fernet(
        base64.urlsafe_b64encode(hmac.digest(key, b"tsunagudake.payout.v1", "sha256"))
    )


def protect_payout(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode("ascii")


def read_payout(ciphertext: str | None) -> str | None:
    return _cipher().decrypt(ciphertext.encode()).decode() if ciphertext else None
