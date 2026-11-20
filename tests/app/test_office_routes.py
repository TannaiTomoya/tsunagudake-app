import pytest
from flask import Flask
from sqlalchemy import select
from test_identity import (
    PASSWORD,
    _publish_and_generate,
    _ranks,
    _seed_candidates,
    _seed_identity,
    sign_in,
    token,
)

from app import create_app
from app.extensions import db
from app.models import (
    AuditLog,
    MemberRole,
    Municipality,
    Office,
    OfficeMember,
    OfficeRoute,
    Role,
    TravelTime,
    User,
)
from app.services.routes import (
    RouteConflict,
    RouteError,
    RouteOfficeNotFound,
    save_route,
)

ROUTES = "/o/1/routes"


@pytest.fixture
def route_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    _seed_identity(app)
    with app.app_context():
        staff = User(email="staff@example.org", role=Role.OFFICE, display_name="架空")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.add(
            Municipality(id=3, code="90001", name="みなみ市", region_group="架空デモ")
        )
        db.session.flush()
        db.session.add_all(
            [
                OfficeMember(
                    user_id=staff.id, office_id=1, member_role=MemberRole.STAFF
                ),
                Office(id=3, name="架空デモ事務所", municipality_id=3, is_demo=True),
                # 他の事務所（事務所2）が確かめた経路。事務所1には混ざらない。
                OfficeRoute(
                    office_id=2, from_municipality_id=2, minutes=8, confirmed_by=1
                ),
            ]
        )
        db.session.commit()
    return app


def _rows(app: Flask, office_id: int = 1) -> list[tuple[int, int, int]]:
    with app.app_context():
        return [
            (row.from_municipality_id, row.minutes, row.confirmed_by)
            for row in db.session.scalars(
                select(OfficeRoute)
                .where(OfficeRoute.office_id == office_id)
                .order_by(OfficeRoute.from_municipality_id)
            )
        ]


def _audits(app: Flask, action: str) -> int:
    with app.app_context():
        return len(
            db.session.scalars(select(AuditLog).where(AuditLog.action == action)).all()
        )


def test_t52_only_the_owner_registers_confirmed_routes(route_app: Flask) -> None:
    owner = route_app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    page = owner.get(ROUTES).get_data(as_text=True)
    assert "まだ登録がありません" in page and 'name="minutes"' in page
    assert "8分" not in page
    # 通常の事務所が選べるのは、通常の町だけ（架空の町は出ない）。
    assert "架空町" in page and "みなみ市" not in page
    csrf = token(owner, ROUTES)

    anonymous = route_app.test_client()
    guest = {
        "csrf_token": token(anonymous, "/login"),
        "municipality": "2",
        "minutes": "7",
    }
    for response in (anonymous.get(ROUTES), anonymous.post(ROUTES, data=guest)):
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login?")

    for wrong in ("0", "301", "1.5", "-5", "三十", "", "1e2", "①", "1000"):
        data = {"csrf_token": csrf, "municipality": "2", "minutes": wrong}
        response = owner.post(ROUTES, data=data)
        assert response.status_code == 422
        assert "1〜300分の整数" in response.get_data(as_text=True)
    # 町が無い・架空の町・選んでいない・項目が無い。
    for town in ("999", "3", "", "0", "abc", None):
        data = {"csrf_token": csrf, "minutes": "30"}
        if town is not None:
            data["municipality"] = town
        response = owner.post(ROUTES, data=data)
        assert response.status_code == 422
        assert "出発の市町村を選んでください" in response.get_data(as_text=True)
    assert owner.post(ROUTES, data={"csrf_token": csrf}).status_code == 422
    assert _rows(route_app) == []

    # 画面から office_id・確かめた人を送っても、URL の事務所とログイン中の人で決まる。
    data = {
        "csrf_token": csrf,
        "municipality": "2",
        "minutes": "２５",
        "office_id": "2",
        "confirmed_by": "4",
        "confirmed_at": "2020-01-01T00:00:00",
    }
    assert owner.post(ROUTES, data=data).status_code == 302
    # 同じ町を登録しても、他の事務所の行は変わらない。
    assert _rows(route_app) == [(2, 25, 1)] and _rows(route_app, 2) == [(2, 8, 1)]
    with route_app.app_context():
        saved = db.session.scalar(select(OfficeRoute).where(OfficeRoute.office_id == 1))
        assert saved is not None and saved.confirmed_at.year >= 2026
    # 同じ町をもう一度登録すると、分を直す（行は増えない）。事務所と同じ町も登録できる。
    for town, minutes in (("2", "40"), ("1", "1"), ("1", "300")):
        data = {"csrf_token": csrf, "municipality": town, "minutes": minutes}
        assert owner.post(ROUTES, data=data).status_code == 302
    assert _rows(route_app) == [(1, 300, 1), (2, 40, 1)]
    assert _audits(route_app, "office_route_saved") == 4
    page = owner.get(ROUTES).get_data(as_text=True)
    assert "架空広域 架空町" in page and "40分" in page and "架空利用者" in page
    assert "8分" not in page

    # 職員と運営は、見るだけ。運営が見たことは記録する。
    for email in ("staff@example.org", "admin@example.org"):
        reader = route_app.test_client()
        assert sign_in(reader, email) == 302
        before = _audits(route_app, "admin_viewed_office")
        page = reader.get(ROUTES).get_data(as_text=True)
        assert "40分" in page and 'name="minutes"' not in page
        viewed = _audits(route_app, "admin_viewed_office") - before
        assert viewed == (1 if email.startswith("admin") else 0)
        data = {
            "csrf_token": token(reader, "/o/1/"),
            "municipality": "2",
            "minutes": "9",
        }
        assert reader.post(ROUTES, data=data).status_code == 403
        assert reader.get("/o/999/routes").status_code == 404
        assert reader.post("/o/999/routes", data=data).status_code == 404
    # 他の事務所・無い事務所は、あることも分からない。
    for path in ("/o/2/routes", "/o/999/routes"):
        assert owner.get(path).status_code == 404
        data = {"csrf_token": csrf, "municipality": "2", "minutes": "9"}
        assert owner.post(path, data=data).status_code == 404
    assert _rows(route_app) == [(1, 300, 1), (2, 40, 1)]
    assert _rows(route_app, 2) == [(2, 8, 1)]

    # 停止した事務所は、代表にも 404。
    with route_app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.is_active = False
        db.session.commit()
    assert owner.get(ROUTES).status_code == 404
    data = {"csrf_token": csrf, "municipality": "2", "minutes": "9"}
    assert owner.post(ROUTES, data=data).status_code == 404
    assert _rows(route_app) == [(1, 300, 1), (2, 40, 1)]


def test_t52_service_refuses_demo_offices_and_fictional_towns(route_app: Flask) -> None:
    with route_app.test_request_context():
        # デモの事務所は、架空の表を使う。架空の町でも、通常の町でも登録しない。
        for town in (3, 1):
            with pytest.raises(RouteConflict):
                save_route(3, 1, town, "30")
        # 通常の事務所に、架空の町は登録しない。
        with pytest.raises(RouteError):
            save_route(1, 1, 3, "30")
        # 桁の多い値・負の値は、町を探す前に落とす。
        for wrong in (10**400, 2**31, -1, 0, None):
            with pytest.raises(RouteError):
                save_route(1, 1, wrong, "30")
        # 無い事務所・停止中の事務所は、画面を通らなくても登録しない。
        with pytest.raises(RouteOfficeNotFound):
            save_route(999, 1, 2, "30")
        office = db.session.get(Office, 1)
        assert office is not None
        office.is_active = False
        db.session.commit()
        with pytest.raises(RouteOfficeNotFound):
            save_route(1, 1, 2, "30")
    assert _rows(route_app) == [] and _rows(route_app, 3) == []


def test_t52_normal_office_ranks_by_its_own_routes_only(route_app: Flask) -> None:
    _seed_candidates(route_app, routes=False)
    with route_app.app_context():
        # 架空の表（travel_times）があっても、通常の事務所は使わない。
        db.session.add(
            TravelTime(from_municipality_id=2, to_municipality_id=1, minutes=20)
        )
        db.session.commit()
    owner = route_app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    _, match_path = _publish_and_generate(owner)
    page = owner.get(match_path).get_data(as_text=True)
    # 同じ町の人も、登録するまでは「分からない」。
    assert _ranks(route_app) == []
    assert "移動時間を登録していない町のつなぎ手は、候補に出ません" in page
    assert f'href="{ROUTES}"' in page

    data = {"csrf_token": token(owner, ROUTES), "municipality": "1", "minutes": "20"}
    assert owner.post(ROUTES, data=data).status_code == 302
    data = {"csrf_token": token(owner, match_path)}
    assert owner.post(match_path, data=data).status_code == 302
    ranked = _ranks(route_app)
    assert ranked and all(
        any("車で20分" in reason for reason in reasons) for *_, reasons in ranked
    )
    # 町2の人は、事務所1の登録が無いので出ない
    # （架空の表では20分、事務所2の経路では8分）。
    with route_app.app_context():
        from app.models import Talent

        towns = {
            db.session.get(Talent, talent_id).municipality_id  # type: ignore[union-attr]
            for _, talent_id, _, _ in ranked
        }
    assert towns == {1}
