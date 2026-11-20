import re
from datetime import timedelta

import pytest
from flask import Flask
from sqlalchemy import func, select
from test_identity import PASSWORD, _seed_identity, sign_in, token

from app import create_app
from app.extensions import db
from app.models import (
    BookkeepingLevel,
    Invitation,
    Municipality,
    Role,
    Talent,
    TalentAffiliation,
    User,
    utc_now,
)
from app.services.accounts import (
    AccountError,
    Invite,
    _hash,
    accept_invitation,
    issue_invitation,
)
from app.services.demo import DemoError, _town
from app.services.municipalities import load_source, prefectures, towns_in
from app.services.trust import ProfileInput, TrustConflict, save_profile
from tsunagu_rules.region import is_fictional

PROFILE = "/t/profile"
INVITE = "/admin/invitations/new"


def _town_app(monkeypatch: pytest.MonkeyPatch, mode: str) -> Flask:
    """通常の町の代わり（00・48 で始まる、実在しないコード）と、架空の町（9）。"""
    monkeypatch.setenv("APP_ENV", mode)
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    _seed_identity(app)
    with app.app_context():
        db.session.add_all(
            [
                Municipality(
                    id=3, code="48001", name="架空港町", region_group="架空県"
                ),
                Municipality(
                    id=4, code="90001", name="みなみ市", region_group="架空広域デモ"
                ),
            ]
        )
        demo = User(
            email="demo-talent@example.org",
            display_name="架空のデモ利用者",
            role=Role.TSUNAGITE,
            is_demo=True,
        )
        demo.set_password(PASSWORD)
        db.session.add(demo)
        db.session.commit()
    return app


@pytest.fixture
def town_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    return _town_app(monkeypatch, "test")


@pytest.fixture
def demo_town_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    # デモのアカウントが入れるのは、demo・development だけ。
    return _town_app(monkeypatch, "development")


def _options(page: str, field: str) -> list[str]:
    select_html = re.search(rf'<select[^>]*name="{field}".*?</select>', page, re.S)
    assert select_html, field
    return [
        label.strip()
        for label in re.findall(r"<option[^>]*>(.*?)</option>", select_html[0], re.S)
    ]


def _profile(csrf: str, town: int) -> dict[str, str | list[str]]:
    return {
        "csrf_token": csrf,
        "municipality": str(town),
        "grade": "2",
        "weekdays": ["1"],
        "affiliation": "graduate",
        "capacity": "2",
    }


def _town_of(app: Flask, email: str) -> int | None:
    with app.app_context():
        return db.session.scalar(
            select(Talent.municipality_id)
            .join(User, User.id == Talent.user_id)
            .where(User.email == email)
        )


def test_t51_source_list_has_every_town_once() -> None:
    rows = load_source()
    codes = [code for code, _, _ in rows]
    assert len(rows) == 1741 and len(set(codes)) == 1741
    assert all(re.fullmatch(r"[0-9]{5}", code) for code in codes)
    assert not any(is_fictional(code) for code in codes)
    assert len({prefecture for _, prefecture, _ in rows}) == 47
    assert {code[:2] for code in codes} == {f"{number:02d}" for number in range(1, 48)}
    # 政令市の区は入れない（市そのものと、東京の特別区は入る）。
    assert sum(name.endswith("区") for _, _, name in rows) == 23


def test_t51_sync_adds_missing_rows_only(town_app: Flask) -> None:
    with town_app.app_context():
        # コードは一覧にあるが、名前が違う行。直さないことを確かめる。
        db.session.add(
            Municipality(code="01100", name="架空の名前", region_group="架空の広域")
        )
        db.session.commit()
    runner = town_app.test_cli_runner()
    first = runner.invoke(args=["sync-municipalities"])
    assert first.exit_code == 0, first.output
    assert "1740件足しました" in first.output and "すでにある1件" in first.output
    assert "違う行が1件" in first.output and "直していません" in first.output
    with town_app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Municipality)) == (
            4 + 1741
        )
        kept = db.session.scalar(
            select(Municipality).where(Municipality.code == "01100")
        )
        assert kept and (kept.name, kept.region_group) == ("架空の名前", "架空の広域")
        # 前からある町（通常の代わり・架空）は、そのまま。
        assert [
            db.session.get(Municipality, key).code  # type: ignore[union-attr]
            for key in (1, 2, 3, 4)
        ] == ["00001", "00002", "48001", "90001"]
        groups = set(
            db.session.scalars(
                select(Municipality.region_group).where(Municipality.id > 5)
            )
        )
        assert len(groups) == 47 and "架空広域" not in groups
    second = runner.invoke(args=["sync-municipalities"])
    assert second.exit_code == 0, second.output
    assert "0件足しました" in second.output and "すでにある1741件" in second.output
    with town_app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Municipality)) == (
            4 + 1741
        )


def test_t51_choices_never_mix_real_and_fictional_towns(town_app: Flask) -> None:
    with town_app.app_context():
        assert [group.key for group in prefectures(demo=False)] == ["00", "48"]
        assert [group.key for group in prefectures(demo=True)] == ["90"]
        # 画面の絞り込みを通らずに呼ばれても、反対側の町は返さない。
        assert [town.code for town in towns_in("48", demo=False)] == ["48001"]
        assert [town.code for town in towns_in("90", demo=True)] == ["90001"]
        assert towns_in("90", demo=False) == []
        assert towns_in("48", demo=True) == []
        for wrong in ("", "4", "480", "4%", "__"):
            assert towns_in(wrong, demo=False) == []


def test_t51_profile_chooses_prefecture_then_town(town_app: Flask) -> None:
    client = town_app.test_client()
    assert sign_in(client, "tsunagite@example.org") == 302
    page = client.get(PROFILE).get_data(as_text=True)
    # 1段目は都道府県。架空の広域（9）は、通常の人には出さない。
    assert _options(page, "prefecture") == ["選択してください", "架空広域", "架空県"]
    assert _options(page, "municipality") == ["選択してください"]
    assert "先に、住む都道府県を選んでください" in page
    assert "onchange" not in page and "<script>" not in page
    page = client.get(f"{PROFILE}?prefecture=48").get_data(as_text=True)
    assert _options(page, "municipality") == ["選択してください", "架空港町"]
    # 架空の広域や、形の違う値を指定しても、町は出ない。
    for wrong in ("90", "4", "48001", "%"):
        page = client.get(PROFILE, query_string={"prefecture": wrong}).get_data(
            as_text=True
        )
        assert _options(page, "municipality") == ["選択してください"]

    csrf = token(client, PROFILE)
    # 選んだ都道府県に無い町・架空の町・「選択してください」は保存しない。
    for town in (1, 4, 0, 999):
        response = client.post(f"{PROFILE}?prefecture=48", data=_profile(csrf, town))
        assert response.status_code == 422
        assert _town_of(town_app, "tsunagite@example.org") is None
    # 架空の広域を指定して架空の町を送っても、通常の人は保存できない。
    response = client.post(f"{PROFILE}?prefecture=90", data=_profile(csrf, 4))
    assert response.status_code == 422
    assert _town_of(town_app, "tsunagite@example.org") is None
    saved = client.post(f"{PROFILE}?prefecture=48", data=_profile(csrf, 3))
    assert saved.status_code == 302
    assert _town_of(town_app, "tsunagite@example.org") == 3
    # 保存した町の都道府県が、次に開いたときの1段目になる。
    page = client.get(PROFILE).get_data(as_text=True)
    assert re.search(r'<option value="48" selected>架空県</option>', page)
    assert re.search(r'<option selected value="3">架空港町</option>', page)


def test_t51_demo_account_chooses_fictional_towns_only(demo_town_app: Flask) -> None:
    client = demo_town_app.test_client()
    # デモのアカウントは /login から入れないので、セッションを直接作る。
    with demo_town_app.app_context():
        demo = db.session.scalar(
            select(User).where(User.email == "demo-talent@example.org")
        )
        assert demo is not None
        identity = demo.get_id()
    with client.session_transaction() as session:
        session["_user_id"] = identity
    page = client.get(PROFILE).get_data(as_text=True)
    # 架空の広域は1つなので、1段目は出さない。
    assert 'name="prefecture"' not in page
    assert _options(page, "municipality") == ["選択してください", "みなみ市"]
    csrf = token(client, PROFILE)
    for query, town in (("", 1), ("?prefecture=00", 1), ("?prefecture=48", 3)):
        response = client.post(PROFILE + query, data=_profile(csrf, town))
        assert response.status_code == 422
        assert _town_of(demo_town_app, "demo-talent@example.org") is None
    assert client.post(PROFILE, data=_profile(csrf, 4)).status_code == 302
    assert _town_of(demo_town_app, "demo-talent@example.org") == 4


@pytest.mark.parametrize(
    ("email", "town"),
    [("tsunagite@example.org", 4), ("demo-talent@example.org", 1)],
)
def test_t51_service_refuses_the_other_side_even_without_the_form(
    town_app: Flask, email: str, town: int
) -> None:
    with town_app.test_request_context():
        user = db.session.scalar(select(User).where(User.email == email))
        assert user is not None
        with pytest.raises(TrustConflict):
            save_profile(
                user.id,
                ProfileInput(
                    municipality_id=town,
                    grade=BookkeepingLevel.NONE,
                    weekdays_mask=1,
                    affiliation=TalentAffiliation.NONE,
                    capacity=1,
                    is_listed=False,
                    tools=frozenset(),
                    strengths=frozenset(),
                ),
            )
    assert _town_of(town_app, email) is None


def test_t51_invitation_and_cli_take_real_towns_only(town_app: Flask) -> None:
    admin = town_app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    page = admin.get(INVITE).get_data(as_text=True)
    assert _options(page, "prefecture") == ["選択してください", "架空広域", "架空県"]
    assert _options(page, "municipality") == ["選択してください"]
    page = admin.get(f"{INVITE}?prefecture=00").get_data(as_text=True)
    assert _options(page, "municipality") == ["選択してください", "架空市", "架空町"]

    def invite(query: str, town: int, email: str) -> int:
        return admin.post(
            INVITE + query,
            data={
                "csrf_token": token(admin, INVITE),
                "email": email,
                "role": "municipality",
                "office": "0",
                "member_role": "staff",
                "municipality": str(town),
            },
        ).status_code

    assert invite("?prefecture=90", 4, "town-a@example.org") == 422
    assert invite("?prefecture=00", 3, "town-b@example.org") == 422
    assert invite("?prefecture=00", 0, "town-c@example.org") == 422
    with town_app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Invitation)) == 0
    assert invite("?prefecture=00", 2, "town-d@example.org") == 302
    with town_app.app_context():
        row = db.session.scalar(select(Invitation))
        assert row and row.municipality_id == 2 and row.role == Role.MUNICIPALITY
    # 画面を通らなくても、架空の町への招待は作らない。
    with town_app.test_request_context(), pytest.raises(AccountError):
        issue_invitation(
            Invite("town-e@example.org", Role.MUNICIPALITY, None, None, 4), 4
        )

    result = town_app.test_cli_runner().invoke(
        args=[
            "create-user",
            "--email",
            "cli@example.org",
            "--display-name",
            "架空担当",
            "--role",
            "tsunagite",
            "--municipality-code",
            "90001",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 1 and "登録済みの市町村コード" in result.output
    with town_app.app_context():
        assert (
            db.session.scalar(select(User).where(User.email == "cli@example.org"))
            is None
        )


def test_t51_demo_side_invites_fictional_towns_only(town_app: Flask) -> None:
    # demo の環境では、反対になる（架空の町だけ）。
    town_app.config["APP_ENV"] = "demo"
    with town_app.test_request_context():
        for town in (1, 3, 0, 999):
            with pytest.raises(AccountError):
                issue_invitation(
                    Invite(
                        "demo-town@example.org", Role.MUNICIPALITY, None, None, town
                    ),
                    4,
                )
        assert db.session.scalar(select(func.count()).select_from(Invitation)) == 0
        issue_invitation(
            Invite("demo-town@example.org", Role.MUNICIPALITY, None, None, 4), 4
        )
        row = db.session.scalar(select(Invitation))
        assert row and row.municipality_id == 4 and row.is_demo


def test_t51_accepting_checks_the_town_again(town_app: Flask) -> None:
    # 決まりが入る前に発行された招待（通常の人に、架空の町）は、受け取れない。
    with town_app.test_request_context():
        for number, town in enumerate((4, 3)):
            db.session.add(
                Invitation(
                    email=f"late{number}@example.org",
                    role=Role.MUNICIPALITY,
                    municipality_id=town,
                    token_hash=_hash(f"late-token-{number}"),
                    expires_at=utc_now() + timedelta(hours=1),
                    is_demo=False,
                    created_by=4,
                )
            )
        db.session.commit()
        with pytest.raises(AccountError):
            accept_invitation("late-token-0", "架空担当", PASSWORD)
        assert (
            db.session.scalar(select(User).where(User.email == "late0@example.org"))
            is None
        )
        accept_invitation("late-token-1", "架空担当", PASSWORD)
        assert db.session.scalar(select(User).where(User.email == "late1@example.org"))


def test_t51_demo_seed_refuses_real_codes(town_app: Flask) -> None:
    with town_app.app_context():
        for code in ("00001", "48001", "89999"):
            with pytest.raises(DemoError, match="9 で始まる"):
                _town(code, "架空の町")
        assert _town("90009", "架空の町").code == "90009"
