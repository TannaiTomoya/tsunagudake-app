import re

import pytest
from flask import Flask
from sqlalchemy import select
from test_identity import PASSWORD, _seed_identity, sign_in, token

from app import create_app
from app.extensions import db
from app.models import AuditLog, MemberRole, Office, OfficeMember, Role, User

PROFILE = "/o/1/profile"
HEADING = "町の会計事務所が支える会社</h2>"


@pytest.fixture
def profile_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    _seed_identity(app)
    with app.app_context():
        staff = User(email="staff@example.org", role=Role.OFFICE, display_name="架空")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.flush()
        db.session.add(
            OfficeMember(user_id=staff.id, office_id=1, member_role=MemberRole.STAFF)
        )
        db.session.commit()
    return app


def _count(app: Flask, office_id: int = 1) -> int | None:
    with app.app_context():
        office = db.session.get(Office, office_id)
        assert office is not None
        return office.client_count


def _audits(app: Flask, action: str) -> list[AuditLog]:
    with app.app_context():
        return list(
            db.session.scalars(select(AuditLog).where(AuditLog.action == action))
        )


def test_t50_only_the_owner_saves_the_client_count(profile_app: Flask) -> None:
    owner = profile_app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    page = owner.get(PROFILE).get_data(as_text=True)
    assert 'name="client_count"' in page and "架空事務所A" in page
    csrf = token(owner, PROFILE)

    # 未ログインは、見るのも直すのもログインへ。
    anonymous = profile_app.test_client()
    guest = {"csrf_token": token(anonymous, "/login"), "client_count": "7"}
    for response in (anonymous.get(PROFILE), anonymous.post(PROFILE, data=guest)):
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login?")
    assert _count(profile_app) is None

    for wrong in ("-1", "1.5", "100001", "多い", "1234567", "①", "㊷", "1e3", "0x10"):
        response = owner.post(PROFILE, data={"csrf_token": csrf, "client_count": wrong})
        assert response.status_code == 422
        assert "0〜100,000の整数" in response.get_data(as_text=True)
        assert _count(profile_app) is None
    # 項目が無い送信では、入っている数を消さない。
    assert owner.post(PROFILE, data={"csrf_token": csrf}).status_code == 422
    for edge, stored in (("0", 0), ("100000", 100_000)):
        saved = owner.post(PROFILE, data={"csrf_token": csrf, "client_count": edge})
        assert saved.status_code == 302 and _count(profile_app) == stored
    assert owner.post(PROFILE, data={"csrf_token": csrf}).status_code == 422
    assert _count(profile_app) == 100_000
    # 画面から office_id を送っても、URL の事務所だけが変わる。
    saved = owner.post(
        PROFILE, data={"csrf_token": csrf, "client_count": " ４２ ", "office_id": "2"}
    )
    assert saved.status_code == 302
    assert _count(profile_app) == 42 and _count(profile_app, 2) is None
    logs = _audits(profile_app, "office_client_count_saved")
    assert len(logs) == 3
    assert all(log.actor_user_id == 1 and log.office_id == 1 for log in logs)
    assert 'value="42"' in owner.get(PROFILE).get_data(as_text=True)

    # 職員と運営は、見るだけ。運営が見たことは記録する。
    for email in ("staff@example.org", "admin@example.org"):
        reader = profile_app.test_client()
        assert sign_in(reader, email) == 302
        before = len(_audits(profile_app, "admin_viewed_office"))
        page = reader.get(PROFILE).get_data(as_text=True)
        assert "42社" in page and 'name="client_count"' not in page
        viewed = len(_audits(profile_app, "admin_viewed_office")) - before
        assert viewed == (1 if email.startswith("admin") else 0)
        data = {"csrf_token": token(reader, "/o/1/"), "client_count": "7"}
        assert reader.post(PROFILE, data=data).status_code == 403
        # 無い事務所は、運営にも 404。
        assert reader.get("/o/999/profile").status_code == 404
        assert reader.post("/o/999/profile", data=data).status_code == 404
        assert _count(profile_app) == 42
    # 他の事務所・無い事務所は、あることも分からない。
    for path in ("/o/2/profile", "/o/999/profile"):
        assert owner.get(path).status_code == 404
        data = {"csrf_token": csrf, "client_count": "7"}
        assert owner.post(path, data=data).status_code == 404
    assert _count(profile_app, 2) is None

    # 空にすると「入力していない」に戻る。
    data = {"csrf_token": csrf, "client_count": ""}
    assert owner.post(PROFILE, data=data).status_code == 302
    assert _count(profile_app) is None

    # 停止した事務所は、代表にも 404。
    with profile_app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.is_active = False
        db.session.commit()
    assert owner.get(PROFILE).status_code == 404
    data = {"csrf_token": csrf, "client_count": "7"}
    assert owner.post(PROFILE, data=data).status_code == 404
    assert _count(profile_app) is None


def _section(page: str) -> str:
    start = page.index(HEADING) + len(HEADING)
    return page[start : page.index("<h2", start)]


@pytest.mark.parametrize(("entered", "shown"), [(4, False), (5, True)])
def test_t50_region_shows_the_client_total_only_from_five_offices(
    profile_app: Flask, entered: int, shown: bool
) -> None:
    with profile_app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.client_count = 30
        db.session.add_all(
            Office(
                name=f"非公開架空事務所{number}",
                municipality_id=1,
                client_count=11 if number < entered - 1 else None,
            )
            for number in range(5)
        )
        # 数えないもの：停止中・デモ・他の町。
        db.session.add_all(
            [
                Office(
                    name="停止中", municipality_id=1, client_count=700, is_active=False
                ),
                Office(name="デモ", municipality_id=1, client_count=800, is_demo=True),
            ]
        )
        other = db.session.get(Office, 2)
        assert other is not None
        other.client_count = 900
        db.session.commit()
    town = profile_app.test_client()
    assert sign_in(town, "municipality@example.org") == 302
    page = town.get("/r/00001/").get_data(as_text=True)
    section = _section(page)
    total = 30 + 11 * (entered - 1)
    if shown:
        assert f"合わせて{total}社を支えています" in section
        assert re.findall(r"\d[\d,]*", section) == [str(total), "5"]
    else:
        assert "入力した事務所が少ないため、表示しません" in section
        assert not re.search(r"\d", section)
    for hidden in ("非公開架空事務所", "架空事務所A", "停止中"):
        assert hidden not in page

    # 町そのものを伏せるときは、見出しも数字も出さない（運営が開いても同じ）。
    admin = profile_app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    small = admin.get("/r/00002/").get_data(as_text=True)
    assert HEADING not in small and "900" not in small


def test_t50_region_says_when_no_office_has_entered(profile_app: Flask) -> None:
    with profile_app.app_context():
        db.session.add_all(
            Office(name=f"非公開架空事務所{number}", municipality_id=1)
            for number in range(5)
        )
        db.session.commit()
    town = profile_app.test_client()
    assert sign_in(town, "municipality@example.org") == 302
    section = _section(town.get("/r/00001/").get_data(as_text=True))
    assert "顧問先の数を入力した事務所は、まだありません" in section


def test_software_is_owner_scoped_and_saved_independently(profile_app: Flask) -> None:
    owner = profile_app.test_client()
    sign_in(owner, "office@example.org")
    csrf = token(owner, PROFILE)
    owner.post(PROFILE, data={"csrf_token": csrf, "client_count": "42"})
    data = {"csrf_token": csrf, "action": "software", "software": "freee"}
    assert owner.post("/o/2/profile", data=data).status_code == 404
    assert owner.post(PROFILE, data=data).status_code == 302
    assert owner.post(PROFILE, data={**data, "software": "invalid"}).status_code == 422
    assert _count(profile_app) == 42
    assert "freee会計" in owner.get("/o/1/").get_data(as_text=True)
    reader = profile_app.test_client()
    sign_in(reader, "staff@example.org")
    assert (
        reader.post(
            PROFILE, data={**data, "csrf_token": token(reader, PROFILE)}
        ).status_code
        == 403
    )
    with profile_app.app_context():
        office = db.session.get(Office, 1)
        assert office and office.software == "freee"


@pytest.mark.parametrize(
    ("software", "label"), [("jdl", "JDL"), ("tkc", "TKC"), ("mjs", "MJS（ミロク）")]
)
def test_office_side_software_is_a_choice(
    profile_app: Flask, software: str, label: str
) -> None:
    owner = profile_app.test_client()
    sign_in(owner, "office@example.org")
    page = owner.get(PROFILE).get_data(as_text=True)
    assert f'<option value="{software}"' in page
    # 顧問先のソフトではなく、事務所のソフトを選ぶ欄だと、その場で読める。
    assert "事務所が記帳・申告に使うソフト" in page
    data = {
        "csrf_token": token(owner, PROFILE),
        "action": "software",
        "software": software,
    }
    assert owner.post(PROFILE, data=data).status_code == 302
    assert label in owner.get("/o/1/").get_data(as_text=True)
    with profile_app.app_context():
        office = db.session.get(Office, 1)
        assert office and office.software == software
