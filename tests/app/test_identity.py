import os
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from app import create_app
from app.extensions import db
from app.models import (
    AuditLog,
    BookkeepingLevel,
    CandidateStatus,
    Engagement,
    LedgerRecord,
    MatchCandidate,
    MatchRun,
    MemberRole,
    Municipality,
    MunicipalityMember,
    Need,
    Office,
    OfficeMember,
    RateLimitEvent,
    Reduction,
    ReductionStatus,
    Role,
    Statement,
    Talent,
    TalentAffiliation,
    TalentSkill,
    TravelTime,
    TrustItem,
    TrustKind,
    User,
    Visit,
    VisitStatus,
    today_in_japan,
)
from tsunagu_rules.types import NeedKind

PASSWORD = "fixture passphrase only"


@pytest.fixture
def identity_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    _seed_identity(app)
    return app


def _seed_identity(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        for index, role in enumerate(Role, start=1):
            user = User(
                id=index,
                email=f"{role}@example.org",
                display_name="架空利用者",
                role=role,
            )
            user.set_password(PASSWORD)
            db.session.add(user)
        db.session.add_all(
            [
                Municipality(
                    id=1, code="90001", name="架空市", region_group="架空広域"
                ),
                Municipality(
                    id=2, code="90002", name="架空町", region_group="架空広域"
                ),
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                Office(id=1, name="架空事務所A", municipality_id=1),
                Office(id=2, name="架空事務所B", municipality_id=2),
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                OfficeMember(office_id=1, user_id=1, member_role=MemberRole.OWNER),
                MunicipalityMember(municipality_id=1, user_id=3),
            ]
        )
        db.session.commit()


def token(client: FlaskClient, path: str) -> str:
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]+value="([^"]+)"', html)
    assert match
    return match[1]


def sign_in(client: FlaskClient, email: str, password: str = PASSWORD) -> int:
    return client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": token(client, "/login"),
        },
    ).status_code


@pytest.mark.parametrize("path", ["/o/1/", "/t/", "/r/90001/"])
def test_t20_anonymous_redirect(identity_app: Flask, path: str) -> None:
    response = identity_app.test_client().get(path)
    assert response.status_code == 302 and response.headers["Location"].startswith(
        "/login?"
    )


@pytest.mark.parametrize(
    ("role", "path", "status"),
    [
        (Role.OFFICE, "/o/1/", 200),
        (Role.OFFICE, "/o/2/", 404),
        (Role.OFFICE, "/o/999/", 404),
        (Role.OFFICE, "/t/", 404),
        (Role.OFFICE, "/r/90001/", 404),
        (Role.TSUNAGITE, "/o/1/", 404),
        (Role.TSUNAGITE, "/t/", 200),
        (Role.MUNICIPALITY, "/o/1/", 404),
        (Role.MUNICIPALITY, "/r/90001/", 200),
        (Role.MUNICIPALITY, "/r/90002/", 404),
        (Role.MUNICIPALITY, "/r/99999/", 404),
        (Role.ADMIN, "/o/1/", 200),
    ],
)
def test_t21_role_and_membership(
    identity_app: Flask, role: Role, path: str, status: int
) -> None:
    client = identity_app.test_client()
    assert sign_in(client, f"{role}@example.org") == 302
    assert client.get(path).status_code == status
    if role == Role.ADMIN:
        with identity_app.app_context():
            row = db.session.scalar(
                select(AuditLog).where(AuditLog.action == "admin_viewed_office")
            )
            assert row and row.actor_user_id == 4 and row.office_id == 1


def test_password_change_revokes_session(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, " OFFICE@example.org ") == 302
    assert client.get("/o/1/").status_code == 200
    with identity_app.app_context():
        user = db.session.scalar(select(User).where(User.id == 1))
        assert user is not None
        user.set_password("updated fixture passphrase")
        db.session.commit()
    assert client.get("/o/1/").status_code == 302
    assert sign_in(client, "office@example.org") == 401


def test_login_audit_and_generic_failure(identity_app: Flask) -> None:
    client = identity_app.test_client()
    for email in ("unknown@example.org", "office@example.org"):
        response = client.post(
            "/login",
            data={
                "email": email,
                "password": "invalid",
                "csrf_token": token(client, "/login"),
            },
        )
        assert response.status_code == 401
        assert "メールアドレスまたはパスワードをご確認ください。" in response.get_data(
            as_text=True
        )
    assert sign_in(client, "office@example.org") == 302
    with identity_app.app_context():
        rows = list(db.session.scalars(select(AuditLog).order_by(AuditLog.id)))
        assert [row.action for row in rows] == ["login_failed", "login_failed", "login"]
        assert [row.actor_user_id for row in rows] == [None, None, 1]
        assert all(
            row.ip_hash and re.fullmatch(r"[0-9a-f]{64}", row.ip_hash) for row in rows
        )


def test_logout_requires_post_and_csrf(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    assert (
        client.post("/logout", data={"csrf_token": token(client, "/o/1/")}).status_code
        == 302
    )
    assert client.get("/o/1/").status_code == 302


def test_create_user_prompts_for_password(identity_app: Flask) -> None:
    result = identity_app.test_cli_runner().invoke(
        args=[
            "create-user",
            "--email",
            "new@example.org",
            "--display-name",
            "架空担当",
            "--role",
            "admin",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    assert PASSWORD not in result.output
    assert sign_in(identity_app.test_client(), "new@example.org") == 302


def _write_need(client: FlaskClient, path: str, body: str, category: str = "") -> str:
    response = client.post(
        path,
        data={
            "csrf_token": token(client, path),
            "body": body,
            "monthly_hours": "30",
            "weekdays": ["0", "2"],
            "manual_kind": category,
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    return response.headers["Location"]


def test_needs_demo_flow(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    assert "顧問先の名前と金額は書かないでください。" in client.get(
        "/o/1/needs/new"
    ).get_data(as_text=True)
    path = _write_need(client, "/o/1/needs/new", "資料が集まらない。過去と見比べる。")
    page = client.get(path).get_data(as_text=True)
    for text in ("資料回収", "チェックと比較", "集まらない", "120,000円", "水曜"):
        assert text in page
    assert (
        client.post(
            path + "/publish", data={"csrf_token": token(client, path)}
        ).status_code
        == 302
    )
    assert client.get(path + "/edit").status_code == 409
    with identity_app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.hourly_rate_yen = 5000
        db.session.commit()
    assert "120,000円" in client.get(path).get_data(as_text=True)
    assert (
        client.post(
            path + "/withdraw", data={"csrf_token": token(client, path)}
        ).status_code
        == 302
    )
    assert "取り下げ済み" in client.get(path).get_data(as_text=True)


def test_t25_block_precedes_manual_choice(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    path = _write_need(client, "/o/1/needs/new", "申告書の作成")
    _write_need(client, path + "/edit", "申告書の作成", "collect")
    assert "「申告書の作成」の部分を確かめてください。" in client.get(path).get_data(
        as_text=True
    )
    csrf = token(client, path + "/edit")
    assert client.post(path + "/publish", data={"csrf_token": csrf}).status_code == 409
    _write_need(client, path + "/edit", "やり方に困っています", "collect")
    assert "手動で選択しました。" in client.get(path).get_data(as_text=True)
    assert client.post(path + "/publish", data={"csrf_token": csrf}).status_code == 302


def test_t21_t22_need_access(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    path = _write_need(client, "/o/1/needs/new", "資料が集まらない")
    csrf = token(client, path + "/edit")
    other = path.replace("/o/1/", "/o/2/", 1)
    assert client.get(other).status_code == 404
    assert client.post(other + "/publish", data={"csrf_token": csrf}).status_code == 404
    with identity_app.app_context():
        member = db.session.scalar(select(OfficeMember))
        assert member is not None
        member.member_role = MemberRole.STAFF
        db.session.commit()
    assert client.get(path).status_code == 200
    assert client.post(path + "/publish", data={"csrf_token": csrf}).status_code == 403
    assert client.post(path + "/withdraw", data={"csrf_token": csrf}).status_code == 403
    admin = identity_app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    assert admin.get(path).status_code == 200
    assert admin.get(path + "/edit").status_code == 403


def test_t23_trust_home_is_scoped_and_uses_inclusive_dates(identity_app: Flask) -> None:
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    with identity_app.app_context():
        other = User(
            email="other-talent@example.org",
            display_name="架空つなぎ手",
            role=Role.TSUNAGITE,
        )
        other.set_password(PASSWORD)
        db.session.add(other)
        db.session.flush()
        own_talent = Talent(
            user_id=2, municipality_id=1, bookkeeping_level=BookkeepingLevel.SECOND
        )
        other_talent = Talent(
            user_id=other.id,
            municipality_id=2,
            bookkeeping_level=BookkeepingLevel.FIRST,
        )
        db.session.add_all([own_talent, other_talent])
        db.session.flush()
        other_id = other_talent.id
        db.session.add_all(
            [
                TrustItem(
                    talent_id=own_talent.id,
                    kind=TrustKind.PLEDGE,
                    valid_from=today - timedelta(days=365),
                    expires_on=today,
                    recorded_by=4,
                ),
                TrustItem(
                    talent_id=own_talent.id,
                    kind=TrustKind.PLEDGE,
                    valid_from=today + timedelta(days=1),
                    expires_on=today + timedelta(days=366),
                    recorded_by=4,
                ),
                TrustItem(
                    talent_id=own_talent.id,
                    kind=TrustKind.TRAINING,
                    valid_from=today - timedelta(days=366),
                    expires_on=today - timedelta(days=1),
                    recorded_by=4,
                ),
            ]
        )
        db.session.commit()
    client = identity_app.test_client()
    assert sign_in(client, "tsunagite@example.org") == 302
    page = client.get(f"/t/?talent_id={other_id}").get_data(as_text=True)
    for expected in (
        "簿記2級",
        "運営の確認を待っています。",
        "あと0日",
        "期限が切れています",
        "0回",
    ):
        assert expected in page
    assert "簿記1級" not in page
    assert client.get(f"/t/{other_id}/").status_code == 404


def test_create_talent_profile_with_registered_municipality(
    identity_app: Flask,
) -> None:
    result = identity_app.test_cli_runner().invoke(
        args=[
            "create-user",
            "--email",
            "profile@example.org",
            "--display-name",
            "架空担当",
            "--role",
            "tsunagite",
            "--municipality-code",
            "90001",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    client = identity_app.test_client()
    assert sign_in(client, "profile@example.org") == 302
    page = client.get("/t/").get_data(as_text=True)
    assert "記録がありません" in page
    assert "候補に出る設定が無効です。" in page


def _seed_candidates(app: Flask) -> None:
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    with app.app_context():
        db.session.add(
            TravelTime(from_municipality_id=2, to_municipality_id=1, minutes=70)
        )
        for index in range(6):
            if index == 0:
                user = db.session.get(User, 2)
                assert user is not None
                user.display_name = "架空候補0"
            else:
                user = User(
                    email=f"talent{index}@example.org",
                    display_name=f"架空候補{index}",
                    role=Role.TSUNAGITE,
                )
                user.set_password(PASSWORD)
                db.session.add(user)
                db.session.flush()
            talent = Talent(
                user_id=user.id,
                municipality_id=2 if index == 3 else 1,
                weekdays_mask=2 if index == 4 else 1,
                is_listed=True,
                capacity=1,
            )
            db.session.add(talent)
            db.session.flush()
            for kind in TrustKind:
                expired = index == 2 and kind == TrustKind.PLEDGE
                db.session.add(
                    TrustItem(
                        talent_id=talent.id,
                        kind=kind,
                        valid_from=today - timedelta(days=366),
                        expires_on=today + timedelta(days=-1 if expired else 1),
                        recorded_by=4,
                    )
                )
        db.session.commit()


def _publish_and_generate(client: FlaskClient) -> tuple[str, str]:
    path = _write_need(client, "/o/1/needs/new", "資料が集まらない。機密の本文です。")
    assert (
        client.post(
            path + "/publish", data={"csrf_token": token(client, path)}
        ).status_code
        == 302
    )
    match_path = path + "/candidates"
    assert (
        client.post(
            match_path, data={"csrf_token": token(client, match_path)}
        ).status_code
        == 302
    )
    return path, match_path


def _offer_data(csrf: str) -> dict[str, str]:
    return {"csrf_token": csrf, "weekday": "0", "share_bp": "4000", "cap_yen": "32000"}


def _ranks(app: Flask) -> list[tuple[int, int, int, list[str]]]:
    with app.app_context():
        run = db.session.scalar(select(MatchRun).order_by(MatchRun.id.desc()).limit(1))
        assert run is not None
        candidates = db.session.scalars(
            select(MatchCandidate)
            .where(MatchCandidate.match_id == run.id)
            .order_by(MatchCandidate.rank)
        )
        return [(row.id, row.talent_id, row.score, row.reasons) for row in candidates]


def test_matching_invite_agreement_and_ledger(identity_app: Flask) -> None:
    _seed_candidates(identity_app)
    office = identity_app.test_client()
    assert sign_in(office, "office@example.org") == 302
    path, match_path = _publish_and_generate(office)
    page = office.get(match_path).get_data(as_text=True)
    for excluded in (
        "守秘の誓約が未有効 1人",
        "移動時間が60分超または不明 1人",
        "希望曜日が合わない 1人",
    ):
        assert excluded in page
    for name in ("架空候補2", "架空候補3", "架空候補4"):
        assert name not in page
    previous = _ranks(identity_app)
    csrf = token(office, match_path)
    assert office.post(match_path, data={"csrf_token": csrf}).status_code == 302
    ranks = _ranks(identity_app)
    assert [row[1:] for row in ranks] == [row[1:] for row in previous]
    first, second, _ = [row[0] for row in ranks]
    first_url = f"{match_path}/{first}/invite"
    second_url = f"{match_path}/{second}/invite"
    assert office.post(first_url, data=_offer_data(csrf)).status_code == 302
    assert office.post(second_url, data=_offer_data(csrf)).status_code == 409
    assert office.get(match_path.replace("/o/1/", "/o/2/")).status_code == 404
    outsider = identity_app.test_client()
    assert sign_in(outsider, "talent1@example.org") == 302
    assert (
        outsider.post(
            f"/t/invitations/{first}/accept",
            data={"csrf_token": token(outsider, "/t/")},
        ).status_code
        == 404
    )
    talent = identity_app.test_client()
    assert sign_in(talent, "tsunagite@example.org") == 302
    offer = talent.get("/t/").get_data(as_text=True)
    assert "あなたへの声かけ" in offer and "資料回収" in offer
    assert "機密の本文" not in offer
    assert talent.get(path).status_code == 404
    answer = f"/t/invitations/{first}/accept"
    response = talent.post(answer, data={"csrf_token": token(talent, "/t/")})
    assert response.status_code == 302
    engagement_path = response.headers["Location"]
    page = talent.get(engagement_path).get_data(as_text=True)
    assert "機密の本文" in page and "顧問先の生データの持ち出し" in page
    assert outsider.get(engagement_path).status_code == 404
    assert (
        talent.post(answer, data={"csrf_token": token(talent, "/t/")}).status_code
        == 404
    )
    office_engagement = engagement_path.replace("/t/", "/o/1/")
    assert office.get(office_engagement).status_code == 200
    assert office.get(office_engagement.replace("/o/1/", "/o/2/")).status_code == 404
    _, later_match_path = _publish_and_generate(office)
    page = office.get(later_match_path).get_data(as_text=True)
    assert "受け持ち上限 1人" in page and "架空候補0" not in page

    runner = identity_app.test_cli_runner()
    assert runner.invoke(args=["verify-ledger"]).exit_code == 0
    with identity_app.app_context():
        assert len(db.session.scalars(select(Engagement)).all()) == 1
        chain = list(
            db.session.scalars(
                select(LedgerRecord)
                .join(Need)
                .where(Need.id == 1)
                .order_by(LedgerRecord.seq)
            )
        )
        assert [row.kind for row in chain] == ["published", "invited", "agreed"]
        chain[-1].payload = chain[-1].payload.replace("持ち出し", "持出し")
        db.session.commit()
    result = runner.invoke(args=["verify-ledger"])
    assert result.exit_code == 1 and "行 3" in result.output


def test_t22_staff_cannot_invite(identity_app: Flask) -> None:
    _seed_candidates(identity_app)
    client = identity_app.test_client()
    assert sign_in(client, "office@example.org") == 302
    _, match_path = _publish_and_generate(client)
    candidate, *_ = _ranks(identity_app)
    csrf = token(client, match_path)
    with identity_app.app_context():
        member = db.session.scalar(select(OfficeMember))
        assert member is not None
        member.member_role = MemberRole.STAFF
        db.session.commit()
    assert client.get(match_path).status_code == 200
    assert (
        client.post(
            f"{match_path}/{candidate[0]}/invite", data=_offer_data(csrf)
        ).status_code
        == 403
    )


@pytest.fixture
def mysql_identity_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    url = make_url(os.environ.get("TEST_DATABASE_URL") or "sqlite://")
    if url.get_backend_name() != "mysql":
        pytest.skip("MySQL のテスト DB が必要です。")
    schema = f"tsunagudake_test_{uuid4().hex}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    try:
        monkeypatch.setenv("APP_ENV", "test")
        app = create_app({"SQLALCHEMY_DATABASE_URI": url.set(database=schema)})
        _seed_identity(app)
        try:
            yield app
        finally:
            with app.app_context():
                db.engine.dispose()
    finally:
        with engine.begin() as conn:
            conn.execute(DropSchema(schema))
        engine.dispose()


def test_t33_mysql_concurrent_invitations(mysql_identity_app: Flask) -> None:
    app = mysql_identity_app
    _seed_candidates(app)
    office = app.test_client()
    assert sign_in(office, "office@example.org") == 302
    _, match_path = _publish_and_generate(office)
    candidate_ids = [row[0] for row in _ranks(app)[:2]]
    barrier = Barrier(2, timeout=10)

    def send(candidate_id: int) -> int:
        client = app.test_client()
        assert sign_in(client, "office@example.org") == 302
        csrf = token(client, match_path)
        barrier.wait()
        return client.post(
            f"{match_path}/{candidate_id}/invite", data=_offer_data(csrf)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, candidate_ids)) == [302, 409]
    with app.app_context():
        invited = db.session.scalars(
            select(MatchCandidate).where(
                MatchCandidate.status == CandidateStatus.INVITED
            )
        ).all()
        assert len(invited) == 1


@pytest.fixture(params=["identity_app", "mysql_identity_app"], ids=["sqlite", "mysql"])
def database_app(request: pytest.FixtureRequest) -> Flask:
    app = request.getfixturevalue(request.param)
    assert isinstance(app, Flask)
    return app


def _agree(app: Flask) -> tuple[FlaskClient, FlaskClient, str, str]:
    _seed_candidates(app)
    office = app.test_client()
    assert sign_in(office, "office@example.org") == 302
    _, match_path = _publish_and_generate(office)
    candidate_id = _ranks(app)[0][0]
    invitation = f"{match_path}/{candidate_id}/invite"
    assert (
        office.post(invitation, data=_offer_data(token(office, match_path))).status_code
        == 302
    )
    talent = app.test_client()
    assert sign_in(talent, "tsunagite@example.org") == 302
    response = talent.post(
        f"/t/invitations/{candidate_id}/accept",
        data={"csrf_token": token(talent, "/t/")},
    )
    assert response.status_code == 302
    talent_path = response.headers["Location"]
    return office, talent, talent_path.replace("/t/", "/o/1/", 1), talent_path


def test_t28_confirmed_visit_is_immutable_and_counted(database_app: Flask) -> None:
    office, talent, office_path, talent_path = _agree(database_app)
    visit_path = talent_path + "/visits/new"
    assert "顧問先の名前と金額は書かない" in talent.get(visit_path).get_data(
        as_text=True
    )
    today = today_in_japan()
    visit = {
        "csrf_token": token(talent, visit_path),
        "day": today.isoformat(),
        "minutes": "60",
        "category": "collect",
        "note": "架空の記録",
    }
    assert (
        talent.post(
            visit_path, data={**visit, "day": (today + timedelta(days=1)).isoformat()}
        ).status_code
        == 409
    )
    assert talent.post(visit_path, data=visit).status_code == 302
    assert talent.post(visit_path, data=visit).status_code == 409
    assert "0回" in talent.get("/t/").get_data(as_text=True)
    with database_app.app_context():
        row = db.session.scalar(select(Visit))
        assert row is not None
        visit_id = row.id
    edit = f"{talent_path}/visits/{visit_id}/edit"
    assert talent.post(edit, data={**visit, "minutes": "75"}).status_code == 302
    assert (
        office.post(
            f"{office_path}/visits/{visit_id}/confirm",
            data={"csrf_token": token(office, office_path)},
        ).status_code
        == 302
    )
    assert "1回" in talent.get("/t/").get_data(as_text=True)
    assert talent.post(edit, data=visit).status_code == 409
    assert "75分" in office.get(office_path).get_data(as_text=True)


def test_t22_t27_finalize_confirmed_hours_once(
    database_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.ledger.utc_now", lambda: datetime(2030, 1, 1, 0, 0, 0, 900000)
    )
    office, talent, office_path, _ = _agree(database_app)
    month = today_in_japan().strftime("%Y-%m")
    csrf = token(office, office_path)
    reduction = {
        "csrf_token": csrf,
        "month": month,
        "category": "collect",
        "before": "30",
        "after": "10",
    }
    endpoint = f"{office_path}/statements/{month}/finalize"
    assert office.post(endpoint, data={"csrf_token": csrf}).status_code == 409
    assert office.post(office_path + "/reductions", data=reduction).status_code == 302
    assert office.post(endpoint, data={"csrf_token": csrf}).status_code == 409
    with database_app.app_context():
        row = db.session.scalar(select(Reduction))
        assert row is not None
        reduction_id = row.id
        member = db.session.scalar(select(OfficeMember))
        assert member is not None
        member.member_role = MemberRole.STAFF
        db.session.commit()
    confirm = f"{office_path}/reductions/{reduction_id}/confirm"
    assert office.post(confirm, data={"csrf_token": csrf}).status_code == 403
    assert office.post(endpoint, data={"csrf_token": csrf}).status_code == 403
    assert (
        talent.post(endpoint, data={"csrf_token": token(talent, "/t/")}).status_code
        == 404
    )
    assert (
        office.post(
            endpoint.replace("/o/1/", "/o/2/"), data={"csrf_token": csrf}
        ).status_code
        == 404
    )
    with database_app.app_context():
        member = db.session.scalar(select(OfficeMember))
        assert member is not None
        member.member_role = MemberRole.OWNER
        db.session.commit()
    assert office.post(confirm, data={"csrf_token": csrf}).status_code == 302
    assert office.post(office_path + "/reductions", data=reduction).status_code == 409
    for _ in range(2):
        assert office.post(endpoint, data={"csrf_token": csrf}).status_code == 302
    page = office.get(office_path + f"?month={month}").get_data(as_text=True)
    assert "確定済みの明細" in page
    assert "<strong>32,000円</strong>" in page
    assert "20時間 × 4,000円 ＝ 80,000円" in page
    with database_app.app_context():
        statements = db.session.scalars(select(Statement)).all()
        assert len(statements) == 1 and statements[0].fee_yen == 32000
        entries = db.session.scalars(
            select(LedgerRecord).where(LedgerRecord.kind == "statement_finalized")
        ).all()
        assert len(entries) == 1
    result = database_app.test_cli_runner().invoke(args=["verify-ledger"])
    assert result.exit_code == 0, result.output

    zero_month = (today_in_japan().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    assert (
        office.post(
            office_path + "/reductions",
            data={**reduction, "month": zero_month, "before": "0", "after": "0"},
        ).status_code
        == 302
    )
    with database_app.app_context():
        zero = db.session.scalar(select(Reduction).where(Reduction.month == zero_month))
        assert zero is not None
        zero_id = zero.id
    assert (
        office.post(
            f"{office_path}/reductions/{zero_id}/confirm", data={"csrf_token": csrf}
        ).status_code
        == 302
    )
    assert (
        office.post(
            f"{office_path}/statements/{zero_month}/finalize", data={"csrf_token": csrf}
        ).status_code
        == 302
    )
    zero_page = office.get(office_path + f"?month={zero_month}").get_data(as_text=True)
    assert (
        "<strong>0円</strong>" in zero_page and "減らなかったので0円です。" in zero_page
    )


def test_t16_t17_t24_region_dashboard(database_app: Flask) -> None:
    _agree(database_app)
    with database_app.app_context():
        db.session.add_all(
            Office(name=f"非公開架空事務所{number}", municipality_id=1)
            for number in range(5)
        )
        db.session.add_all(
            [
                Office(name="非公開架空事務所・隣町", municipality_id=2),
                Office(
                    name="非公開架空事務所・休止", municipality_id=1, is_active=False
                ),
            ]
        )
        for talent in db.session.scalars(
            select(Talent).where(Talent.municipality_id == 1)
        ):
            talent.affiliation = TalentAffiliation.KYORYOKUTAI
        engagement = db.session.scalar(select(Engagement))
        assert engagement is not None
        day = today_in_japan()
        for index, status in enumerate(
            [VisitStatus.CONFIRMED] * 5 + [VisitStatus.RECORDED, VisitStatus.DISPUTED]
        ):
            db.session.add(
                Visit(
                    engagement_id=engagement.id,
                    visit_date=day - timedelta(days=index),
                    minutes=60,
                    category=NeedKind.COLLECT,
                    status=status,
                    note="非公開の架空メモ",
                )
            )
        db.session.add_all(
            Reduction(
                engagement_id=engagement.id,
                month=day.strftime("%Y-%m"),
                category=category,
                before_hours=before,
                after_hours=after,
                status=status,
            )
            for category, before, after, status in (
                (NeedKind.COLLECT, 30, 10, ReductionStatus.CONFIRMED),
                (NeedKind.CHECK, 9, 2, ReductionStatus.CONFIRMED),
                (NeedKind.ENTRY, 1, 7, ReductionStatus.CONFIRMED),
                (NeedKind.REPORT, 50, 0, ReductionStatus.DRAFT),
            )
        )
        db.session.commit()

    town = database_app.test_client()
    assert sign_in(town, "municipality@example.org") == 302
    response = town.get("/r/90001/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    own_row = page.split("<tbody>")[1].split("</tbody>")[0]
    cells = re.findall(r"<td data-numeric>(.*?)</td>", own_row, flags=re.S)
    assert [cell.strip() for cell in cells] == ["6", "5", "5", "5", "27時間"]
    assert "少ないため表示しません" in page.split("<tfoot>")[1]
    for private in (
        "架空町",
        "架空事務所",
        "架空候補",
        "機密の本文",
        "非公開の架空メモ",
    ):
        assert private not in page
    assert town.get("/r/90002/").status_code == 404
    for account in ("office", "tsunagite"):
        outsider = database_app.test_client()
        assert sign_in(outsider, f"{account}@example.org") == 302
        assert outsider.get("/r/90001/").status_code == 404

    admin = database_app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    response = admin.get("/r/90002/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert (
        page.split("<tbody>")[1].split("</tbody>")[0].count("少ないため表示しません")
        == 5
    )
    assert "少ないため表示しません" in page.split("<tfoot>")[1]

    with database_app.app_context():
        db.session.add_all(
            Office(name=f"非公開架空事務所・隣町{number}", municipality_id=2)
            for number in range(3)
        )
        db.session.commit()
    page = town.get("/r/90001/").get_data(as_text=True)
    total = page.split("<tfoot>")[1].split("</tfoot>")[0]
    assert "架空広域の合計" in total
    assert ">11<" in total.strip().replace(" ", "").replace("\n", "")
    assert "架空町" not in page


def test_t34_email_lock_expires_and_success_clears_its_failures(
    database_app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2030, 1, 1)
    monkeypatch.setattr("app.ratelimit.utc_now", lambda: now)
    client = database_app.test_client()
    email = "office@example.org"
    assert sign_in(client, email, "wrong") == 401
    assert sign_in(client, email) == 302
    with database_app.app_context():
        events = db.session.scalars(select(RateLimitEvent)).all()
        assert len(events) == 1 and events[0].scope == "ip"
        assert re.fullmatch(r"[0-9a-f]{64}", events[0].key_hash)
    now += timedelta(days=2)
    assert sign_in(client, email, "wrong") == 401
    now += timedelta(minutes=4)
    for _ in range(4):
        assert sign_in(client, " OFFICE@example.org ", "wrong") == 401
    assert sign_in(client, email) == 429
    now += timedelta(minutes=14, seconds=59)
    response = client.post(
        "/login",
        data={
            "email": email,
            "password": PASSWORD,
            "csrf_token": token(client, "/login"),
        },
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    now += timedelta(seconds=1)
    assert sign_in(client, email) == 302
    with database_app.app_context():
        failures = db.session.scalars(select(RateLimitEvent)).all()
        assert len(failures) == 5 and all(row.scope == "ip" for row in failures)


def test_t34_proxy_ip_is_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "PROXY_FIX": True})
    _seed_identity(app)
    client = app.test_client()
    csrf = token(client, "/login")

    def post(email: str, ip: str, password: str) -> int:
        return client.post(
            "/login",
            headers={"X-Forwarded-For": ip},
            data={"email": email, "password": password, "csrf_token": csrf},
        ).status_code

    for index in range(20):
        assert post(f"unknown{index}@example.org", "192.0.2.1", "wrong") == 401
    assert post("office@example.org", "192.0.2.1", PASSWORD) == 429
    assert post("office@example.org", "192.0.2.2", PASSWORD) == 302


@pytest.mark.parametrize("scope", ["email", "ip"])
def test_t34_parallel_attempts_cannot_exceed_limit(
    mysql_identity_app: Flask, scope: str
) -> None:
    mysql_identity_app.config["RATE_LOGIN_IP_MAX"] = 5
    barrier = Barrier(6, timeout=10)

    def attempt(index: int) -> int:
        client = mysql_identity_app.test_client()
        csrf = token(client, "/login")
        barrier.wait()
        return client.post(
            "/login",
            environ_overrides={
                "REMOTE_ADDR": f"192.0.2.{index + 1 if scope == 'email' else 1}"
            },
            data={
                "email": "office@example.org"
                if scope == "email"
                else f"unknown{index}@example.org",
                "password": "wrong",
                "csrf_token": csrf,
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sorted(pool.map(attempt, range(6))) == [401] * 5 + [429]


def test_talent_profile_pledge_and_admin_review(database_app: Flask) -> None:
    from app.services.trust import PLEDGE_VERSION

    talent = database_app.test_client()
    admin = database_app.test_client()
    assert sign_in(talent, "tsunagite@example.org") == 302
    assert sign_in(admin, "admin@example.org") == 302
    profile = {
        "csrf_token": token(talent, "/t/profile"),
        "municipality": "1",
        "grade": "2",
        "weekdays": ["1", "3"],
        "affiliation": "graduate",
        "capacity": "3",
        "listed": "y",
        "tools": ["excel", "python"],
        "strengths": ["collect", "check"],
        "level_verified_at": "2030-01-01",
        "user_id": "4",
        "role": "admin",
    }
    assert talent.post("/t/profile", data=profile).status_code == 302
    with database_app.app_context():
        row = db.session.scalar(select(Talent).where(Talent.user_id == 2))
        assert row is not None
        assert row.level_verified_at is None
        assert row.capacity == 3 and row.weekdays_mask == 10 and row.is_listed
        assert sorted(db.session.scalars(select(TalentSkill.tag)).all()) == [
            "check",
            "collect",
            "excel",
            "python",
        ]
        profile_id = row.id
    review = f"/admin/talents/{profile_id}"
    grade = review + "/verify-grade"
    training = review + "/training"
    assert (
        talent.post(
            grade, data={"csrf_token": profile["csrf_token"], "grade": "2"}
        ).status_code
        == 403
    )
    assert (
        talent.post(training, data={"csrf_token": profile["csrf_token"]}).status_code
        == 403
    )
    assert talent.get(review).status_code == 404
    assert admin.get("/t/profile").status_code == 404
    csrf = token(admin, review)
    assert admin.post(grade, data={"csrf_token": csrf, "grade": "1"}).status_code == 409
    assert admin.post(grade, data={"csrf_token": csrf, "grade": "2"}).status_code == 302
    assert "運営が確認済みです" in talent.get("/t/").get_data(as_text=True)
    today = today_in_japan()
    assert (
        admin.post(
            training, data={"csrf_token": csrf, "day": today.isoformat()}
        ).status_code
        == 302
    )
    assert (
        admin.post(
            training,
            data={
                "csrf_token": csrf,
                "day": (today + timedelta(days=1)).isoformat(),
            },
        ).status_code
        == 422
    )

    pledge = "/t/pledge"
    assert "顧問先の生データ" in talent.get(pledge).get_data(as_text=True)
    form = {"csrf_token": token(talent, pledge), "version": PLEDGE_VERSION}
    assert talent.post(pledge, data=form).status_code == 422
    assert (
        talent.post(pledge, data={**form, "agree": "y", "version": "old"}).status_code
        == 422
    )
    for _ in range(2):
        assert talent.post(pledge, data={**form, "agree": "y"}).status_code == 302
    assert talent.post("/t/profile", data={**profile, "grade": "3"}).status_code == 302
    with database_app.app_context():
        row = db.session.get(Talent, profile_id)
        assert (
            row is not None
            and row.level_verified_at is None
            and row.level_verified_by is None
        )
        items = {item.kind: item for item in db.session.scalars(select(TrustItem))}
        assert len(db.session.scalars(select(TrustItem)).all()) == 2
        assert items[TrustKind.PLEDGE].pledge_version == PLEDGE_VERSION
        assert items[TrustKind.PLEDGE].recorded_by == 2
        assert items[TrustKind.TRAINING].recorded_by == 4
        assert items[TrustKind.PLEDGE].expires_on == today + timedelta(days=365)


@pytest.mark.parametrize("role", [Role.OFFICE, Role.MUNICIPALITY])
def test_profile_and_admin_reject_other_roles(identity_app: Flask, role: Role) -> None:
    client = identity_app.test_client()
    assert sign_in(client, f"{role}@example.org") == 302
    for path in (
        "/t/profile",
        "/t/pledge",
        "/admin/",
        "/admin/ledger",
        "/admin/talents/1",
    ):
        assert client.get(path).status_code == 404


def test_admin_audits_office_view_and_revokes_account(database_app: Flask) -> None:
    office, talent, _, _ = _agree(database_app)
    admin = database_app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    assert office.get("/admin/ledger").status_code == 404
    assert admin.get("/o/1/").status_code == 200
    assert "台帳の検証が完了しました" in admin.get("/admin/ledger").get_data(
        as_text=True
    )
    csrf = token(admin, "/admin/")
    assert (
        admin.post("/admin/users/4/suspend", data={"csrf_token": csrf}).status_code
        == 403
    )
    assert (
        admin.post("/admin/users/2/suspend", data={"csrf_token": csrf}).status_code
        == 302
    )
    assert talent.get("/t/").status_code == 302
    assert sign_in(talent, "tsunagite@example.org") == 401
    with database_app.app_context():
        row = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "admin_viewed_office")
        )
        assert row and row.actor_user_id == 4 and row.office_id == 1
        record = db.session.scalar(
            select(LedgerRecord).where(LedgerRecord.kind == "agreed")
        )
        assert record is not None
        record.entry_hash = "0" * 64
        db.session.commit()
    assert "記録が一致しません" in admin.get("/admin/ledger").get_data(as_text=True)
