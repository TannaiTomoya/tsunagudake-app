from secrets import token_hex

import pytest
from sqlalchemy import select
from test_identity import _ranks, token

from app import create_app
from app.extensions import db
from app.models import Engagement, Need, NeedStatus, Office, Role, User, today_in_japan
from app.services.demo import ACCOUNTS, SCRIPT_BODY

PASSPHRASE = "fixture demo passphrase"


def test_t29_production_has_no_demo_entry_or_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app(
        {
            "APP_ENV": "production",
            "DEBUG": False,
            "TESTING": False,
            "SECRET_KEY": token_hex(32),
            "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://root@localhost/test",
            "BASE_URL": "https://localhost",
            "SESSION_COOKIE_SECURE": True,
            "AI_DRIVER": "off",
        }
    )
    client = app.test_client()
    assert client.get("/demo/enter").status_code == 404
    assert client.post("/demo/enter").status_code == 404
    assert b"/demo/enter" not in client.get("/").data
    assert client.get("/how").status_code == 200
    for args in (["seed-demo"], ["reset-demo", "--yes"]):
        result = app.test_cli_runner().invoke(args=args)
        assert result.exit_code == 1
        assert "demo・development" in result.output


def test_demo_rehearsal_before_and_after_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "DEMO_PASSWORD": PASSPHRASE,
        }
    )
    with app.app_context():
        db.create_all()
        db.session.add(
            User(
                email="ordinary@example.org",
                display_name="通常の架空利用者",
                role=Role.OFFICE,
                password_hash="unused",
                session_nonce=token_hex(16),
            )
        )
        db.session.commit()
    runner = app.test_cli_runner()
    for args in (["seed-demo"], ["seed-demo"], ["verify-ledger"]):
        result = runner.invoke(args=args)
        assert result.exit_code == 0, result.output
    assert runner.invoke(args=["reset-demo"]).exit_code == 1

    def rehearse() -> None:
        office = app.test_client()
        entry = "/demo/enter"
        page = office.get(entry).get_data(as_text=True)
        assert "会計事務所として見る" in page and "自治体として見る" in page
        assert PASSPHRASE not in page
        csrf = token(office, entry)
        assert (
            office.post(
                entry, data={"role": "office", "password": "wrong", "csrf_token": csrf}
            ).status_code
            == 401
        )
        response = office.post(
            entry, data={"role": "office", "password": PASSPHRASE, "csrf_token": csrf}
        )
        assert response.status_code == 302
        home = response.headers["Location"].rstrip("/")
        assert len(home.split("/")) == 3
        with app.app_context():
            seeded_draft = db.session.scalar(
                select(Need).where(Need.status == NeedStatus.DRAFT)
            )
            assert seeded_draft and seeded_draft.body == SCRIPT_BODY
            assert (
                len(
                    db.session.scalars(
                        select(Office).where(Office.is_demo.is_(True))
                    ).all()
                )
                == 8
            )
        form = home + "/needs/new"
        response = office.post(
            form,
            data={
                "body": SCRIPT_BODY,
                "monthly_hours": "30",
                "weekdays": "1",
                "csrf_token": token(office, form),
            },
        )
        assert response.status_code == 302
        path = response.headers["Location"]
        page = office.get(path).get_data(as_text=True)
        assert "資料回収" in page and "チェックと比較" in page and "120,000円" in page
        assert (
            office.post(
                path + "/publish", data={"csrf_token": token(office, path)}
            ).status_code
            == 302
        )
        match_path = path + "/candidates"
        csrf = token(office, match_path)
        assert office.post(match_path, data={"csrf_token": csrf}).status_code == 302
        page = office.get(match_path).get_data(as_text=True)
        for excluded in (
            "守秘の誓約 1人",
            "移動時間 1人",
            "曜日 1人",
            "受け持ち上限 1人",
        ):
            assert excluded in page
        ranks = _ranks(app)
        assert len(ranks) == 3
        candidate = ranks[0][0]
        assert (
            office.post(
                f"{match_path}/{candidate}/invite",
                data={"csrf_token": csrf, "weekday": "1", "share_bp": "4000"},
            ).status_code
            == 302
        )

        talent = app.test_client()
        assert (
            talent.post(
                entry,
                data={
                    "role": "tsunagite",
                    "password": PASSPHRASE,
                    "csrf_token": token(talent, entry),
                },
            ).status_code
            == 302
        )
        agreed = talent.post(
            f"/t/invitations/{candidate}/accept",
            data={"csrf_token": token(talent, "/t/")},
        )
        assert agreed.status_code == 302
        page = talent.get(agreed.headers["Location"]).get_data(as_text=True)
        assert "顧問先の生データの持ち出し" in page

        with app.app_context():
            office_id = int(home.rsplit("/", 1)[1])
            example_id = db.session.scalar(
                select(Engagement.id)
                .where(Engagement.office_id == office_id)
                .order_by(Engagement.id)
                .limit(1)
            )
            assert example_id is not None
        example = f"{home}/engagements/{example_id}"
        assert "<strong>32,000円</strong>" in office.get(example).get_data(as_text=True)
        month = today_in_japan().strftime("%Y-%m")
        assert (
            office.post(
                f"{example}/statements/{month}/finalize",
                data={"csrf_token": token(office, example)},
            ).status_code
            == 302
        )
        assert "確定済みの明細" in office.get(example).get_data(as_text=True)

        town = app.test_client()
        response = town.post(
            entry,
            data={
                "role": "municipality",
                "password": PASSPHRASE,
                "csrf_token": token(town, entry),
            },
        )
        assert response.status_code == 302
        page = town.get(response.headers["Location"]).get_data(as_text=True)
        assert (
            "みなみ市" in page and "60時間" in page and "少ないため表示しません" in page
        )
        assert runner.invoke(args=["verify-ledger"]).exit_code == 0

    rehearse()
    result = runner.invoke(args=["reset-demo", "--yes"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert (
            db.session.scalar(select(User).where(User.email == "ordinary@example.org"))
            is not None
        )
        assert (
            db.session.scalar(select(User).where(User.email == ACCOUNTS[Role.OFFICE]))
            is not None
        )
    rehearse()
