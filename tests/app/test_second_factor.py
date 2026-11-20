import base64
import re

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from flask import Flask
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app
from test_identity import sign_in, token

from app.extensions import db
from app.models import SecondFactor, User


def test_t44_password_alone_and_legacy_session_cannot_open_office(
    database_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_app.config["MFA_REQUIRED"] = True
    now = 1830000000
    monkeypatch.setattr("app.services.second_factor.time", lambda: now)
    challenge = "/login/second-factor"
    client = database_app.test_client()
    old = database_app.test_client()
    with database_app.app_context():
        user = db.session.get(User, 1)
        assert user
        with old.session_transaction() as session:
            session["_user_id"] = user.get_id()
    assert old.get("/o/1/").status_code == 302
    assert client.get(challenge).status_code == 302
    assert sign_in(client, "office@example.org") == 302
    assert client.get("/o/1/").status_code == 302
    page = client.get(challenge)
    key = re.search(r'id="setup-key">([A-Z2-7]+)<', page.get_data(as_text=True))
    assert key and page.headers["Cache-Control"] == "no-store"
    generator = TOTP(base64.b32decode(key[1]), 6, hashes.SHA1(), 30)
    otp = generator.generate(now).decode()

    def post(value: str):  # type: ignore[no-untyped-def]
        return client.post(
            challenge, data={"csrf_token": token(client, challenge), "code": value}
        )

    assert post("bad").status_code == 401
    response = post(otp)
    assert response.status_code == 200
    codes = re.findall(r"<code>([A-F0-9]{16})</code>", response.get_data(as_text=True))
    assert len(set(codes)) == 8
    assert (
        client.get("/o/1/").status_code == 200
        and client.get("/o/2/").status_code == 404
    )
    with database_app.app_context():
        factor = db.session.get(SecondFactor, 1)
        assert (
            factor
            and key[1] not in factor.secret_cipher
            and codes[0] not in factor.recovery_hashes
        )
    assert sign_in(client, "office@example.org") == 302
    assert post(otp).status_code == 401
    assert post(codes[0]).status_code == 302
    assert sign_in(client, "office@example.org") == 302
    assert post(codes[0]).status_code == 401
    now += 30
    assert post(generator.generate(now).decode()).status_code == 302
    assert client.get("/o/1/").status_code == 200
