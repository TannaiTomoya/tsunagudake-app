import re
from secrets import token_hex

import pytest
from sqlalchemy import select
from test_identity import _ranks, token

from app import create_app
from app.extensions import db
from app.models import (
    Engagement,
    MatchCandidate,
    Need,
    NeedStatus,
    Office,
    Role,
    Talent,
    User,
    today_in_japan,
)
from app.services.demo import ACCOUNTS, SCRIPT_BODY

PASSPHRASE = "fixture demo passphrase"


def primary_action_labels(html: str) -> list[str]:
    main = html.split("<main ", 1)[1].split("</main>", 1)[0]
    return [
        label
        for element, attributes, label in re.findall(
            r"<(a|button)\b([^>]*)>([^<]*)</\1>", main
        )
        if (element == "button" or 'role="button"' in attributes)
        and "secondary" not in attributes
        and "disabled" not in attributes
    ]


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

    def rehearse(weekday: int) -> None:
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
                "weekdays": str(weekday),
                "csrf_token": token(office, form),
            },
        )
        assert response.status_code == 302
        path = response.headers["Location"]
        page = office.get(path).get_data(as_text=True)
        assert (
            "資料回収" in page and "チェックと比較" not in page and "120,000円" in page
        )
        assert (
            office.post(
                path + "/publish", data={"csrf_token": token(office, path)}
            ).status_code
            == 302
        )
        detail = office.get(path).get_data(as_text=True)
        assert 'data-mood="happy"' in detail
        assert primary_action_labels(detail) == ["候補と理由を見る"]
        assert (
            '<button type="submit" class="secondary outline">取り下げる</button>'
            in detail
        )
        match_path = path + "/candidates"
        csrf = token(office, match_path)
        assert office.post(match_path, data={"csrf_token": csrf}).status_code == 302
        page = office.get(match_path).get_data(as_text=True)
        for excluded in (
            "守秘の誓約が未有効 1人",
            "移動時間が60分超または不明 1人",
            "希望曜日が合わない 1人" if weekday == 1 else "希望曜日が合わない ",
            "受け持ち上限 1人",
        ):
            assert excluded in page
        ranks = _ranks(app)
        assert [row[2] for row in ranks] == [84, 36, 36]
        assert "資料回収が得意です" in page
        assert "所属：地域おこし協力隊" in page
        candidate = ranks[0][0]
        selected = re.findall(r'<option value="(\d)" selected>', page)
        assert selected == [str(weekday)] * 3
        assert page.count('name="share_percent"') == 3
        order = office.get(home + "/").get_data(as_text=True)
        phases = re.findall(r'class="state" data-status="([^"]+)"', order)
        assert phases == ["engaged", "published", "draft", "blocked"]
        assert "明細を見る" in order and "候補を見る" in order
        invitation_path = f"{match_path}/{candidate}/invite"
        for percent in ("0", "50", "4000", "40.5"):
            response = office.post(
                invitation_path,
                data={
                    "csrf_token": csrf,
                    "weekday": str(weekday),
                    "share_percent": percent,
                },
            )
            assert response.status_code == 422
            assert "1〜49の整数" in response.get_data(as_text=True)
        response = office.post(
            invitation_path,
            data={
                "csrf_token": csrf,
                "weekday": "0",
                "share_percent": "40",
            },
        )
        assert response.status_code == 409
        assert "双方が通える曜日" in response.get_data(as_text=True)
        assert (
            office.post(
                f"{match_path}/{candidate}/invite",
                data={
                    "csrf_token": csrf,
                    "weekday": str(weekday),
                    "share_percent": "40",
                },
            ).status_code
            == 302
        )

        with app.app_context():
            offer = db.session.get(MatchCandidate, candidate)
            assert offer is not None and offer.offer_fee_share_bp == 4000
            assert offer.offer_weekday == weekday
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
        detail = office.get(path).get_data(as_text=True)
        assert primary_action_labels(detail) == ["伴走と境界線台帳を見る"]

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
        home_page = office.get(home + "/").get_data(as_text=True)
        assert (
            re.findall(r'<a href="([^"]+)">明細を見る</a>', home_page)[0]
            == example + "#statement"
        )
        statement = office.get(example).get_data(as_text=True)
        assert all(
            value in statement
            for value in ("20時間", "80,000円", "<strong>32,000円</strong>")
        )
        month = today_in_japan().strftime("%Y-%m")
        assert (
            office.post(
                f"{example}/statements/{month}/finalize",
                data={
                    "csrf_token": token(office, example),
                    "returned_to": "顧問先の相談に戻った",
                },
            ).status_code
            == 302
        )
        page = office.get(example).get_data(as_text=True)
        assert "確定済みの明細" in page and "顧問先の相談に戻った" in page

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
        assert "資料回収の時間が60時間減り、判断と相談に戻りました" in page
        assert "つなぎ手6人のうち、地域おこし協力隊は5人です" in page
        assert runner.invoke(args=["verify-ledger"]).exit_code == 0

    rehearse(1)
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
    rehearse(2)


def test_demo_weekdays_and_candidate_specific_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    app = create_app(
        {"SQLALCHEMY_DATABASE_URI": "sqlite://", "DEMO_PASSWORD": PASSPHRASE}
    )
    with app.app_context():
        db.create_all()
    assert app.test_cli_runner().invoke(args=["seed-demo"]).exit_code == 0
    client = app.test_client()
    entry = "/demo/enter"
    home = client.post(
        entry,
        data={
            "role": "office",
            "password": PASSPHRASE,
            "csrf_token": token(client, entry),
        },
    ).headers["Location"]
    new_need = home + "needs/new"
    csrf = token(client, new_need)

    def generate_for(days: list[int]) -> str:
        path = client.post(
            new_need,
            data={
                "csrf_token": csrf,
                "body": SCRIPT_BODY,
                "monthly_hours": "30",
                "weekdays": [str(day) for day in days],
            },
        ).headers["Location"]
        match_path = path + "/candidates"
        for url in (path + "/publish", match_path):
            assert client.post(url, data={"csrf_token": csrf}).status_code == 302
        return client.get(match_path).get_data(as_text=True)

    for day in range(5):
        page = generate_for([day])
        assert [r[2] for r in _ranks(app)] == [84, 36, 36]
        assert re.findall(r"<td>架空つなぎ手 (\d+)（", page) == ["1", "2", "3"]
        assert re.findall(r'<option value="(\d)" selected>', page) == [str(day)] * 3
    page = generate_for([6])
    assert not _ranks(app)
    assert "条件に合う人が、今はいません" in page and "希望曜日が合わない" in page

    # Each form must show the intersection for its own candidate.
    with app.app_context():
        talent = db.session.scalar(
            select(Talent)
            .join(User, User.id == Talent.user_id)
            .where(User.email == "talent2@tsunagu.example")
        )
        assert talent is not None
        talent.weekdays_mask = 1 << 2
        db.session.commit()
    page = generate_for([1, 2])
    forms = dict(
        re.findall(
            r'<form method="post" action="([^"]+/invite)">(.*?)</form>', page, re.S
        )
    )
    by_day = {}
    for action, html in forms.items():
        choices = re.findall(r'<option value="(\d)"( selected)?>', html)
        by_day[action] = choices
    assert [("1", " selected"), ("2", "")] in by_day.values()
    wed_only = next(
        action for action, choices in by_day.items() if choices == [("2", " selected")]
    )
    response = client.post(
        wed_only, data={"csrf_token": csrf, "weekday": "1", "share_percent": "40"}
    )
    assert response.status_code == 409 and "双方が通える曜日" in response.get_data(
        as_text=True
    )
