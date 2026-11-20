import json
from datetime import timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select
from test_identity import PASSWORD, _agree, sign_in, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    AuditLog,
    Engagement,
    EngagementStatus,
    LedgerRecord,
    MemberRole,
    MunicipalityMember,
    Need,
    NeedStatus,
    Office,
    OfficeMember,
    Reduction,
    Role,
    Statement,
    StatementStatus,
    Talent,
    TalentSkill,
    TalentSkillKind,
    TrustItem,
    User,
    Visit,
    today_in_japan,
    utc_now,
)
from app.services.ledger import ledger_failures
from app.services.withdrawal import WithdrawalBlocked, withdraw
from tsunagu_rules.types import NeedKind

WITHDRAW = "/account/withdraw"


def _end(client: FlaskClient, path: str, reason: str = "goal_met") -> int:
    return client.post(
        path + "/end", data={"csrf_token": token(client, path), "reason": reason}
    ).status_code


def _leave(client: FlaskClient, password: str = PASSWORD, confirm: str = "yes") -> int:
    data = {"csrf_token": token(client, "/account"), "password": password}
    if confirm:
        data["confirm"] = confirm
    return client.post(WITHDRAW, data=data).status_code


def _user(app: Flask, user_id: int) -> tuple[str, str, bool]:
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user is not None
        return user.email, user.display_name, user.is_active


@pytest.mark.parametrize("side", ["office", "talent"])
def test_t53_either_side_ends_the_engagement_once(
    database_app: Flask, side: str
) -> None:
    office, talent, office_path, talent_path = _agree(database_app)
    client, path = (office, office_path) if side == "office" else (talent, talent_path)
    # 理由は選択肢だけ。自由な文は受け取らない。
    assert _end(client, path, "顧問先の都合") == 409
    assert _end(client, path, "") == 409
    with database_app.app_context():
        assert db.session.scalar(select(Engagement.status)) == EngagementStatus.TRIAL
    assert _end(client, path) == 302
    assert _end(client, path, "other") == 302
    with database_app.app_context():
        engagement = db.session.scalar(select(Engagement))
        assert engagement is not None
        assert engagement.status == EngagementStatus.ENDED
        assert engagement.end_reason == "目的を果たした" and engagement.ended_at
        need = db.session.get(Need, engagement.need_id)
        assert need is not None and need.status == NeedStatus.CLOSED
        ended = db.session.scalars(
            select(LedgerRecord).where(LedgerRecord.kind == "ended")
        ).all()
        assert len(ended) == 1
        assert json.loads(ended[0].payload)["data"] == {
            "reason": "goal_met",
            "by": side,
        }
        assert ledger_failures() == []
    page = office.get(office_path).get_data(as_text=True)
    assert "この伴走は終わりました（目的を果たした）" in page and "伴走を終了" in page
    assert "この伴走を終える" not in page
    # 終わった伴走には、新しい訪問を記録できない。
    visit_path = talent_path + "/visits/new"
    visit = {
        "csrf_token": token(talent, visit_path),
        "day": today_in_japan().isoformat(),
        "minutes": "60",
        "category": "collect",
    }
    assert talent.post(visit_path, data=visit).status_code == 409
    assert "訪問を記録する</a>" not in talent.get(talent_path).get_data(as_text=True)


def test_t54_withdrawal_waits_for_open_work_and_keeps_shared_records(
    database_app: Flask,
) -> None:
    app = database_app
    office, talent, office_path, talent_path = _agree(app)
    month = today_in_japan().strftime("%Y-%m")
    with app.app_context():
        engagement = db.session.scalar(select(Engagement))
        assert engagement is not None
        engagement_id, talent_id = engagement.id, engagement.talent_id
        db.session.add_all(
            [
                TalentSkill(
                    talent_id=talent_id, kind=TalentSkillKind.TOOL, tag="freee"
                ),
                Visit(
                    engagement_id=engagement_id,
                    visit_date=today_in_japan(),
                    minutes=60,
                    category=NeedKind.COLLECT,
                    note="架空のメモ",
                    learned_term="月締め箱",
                    idea="架空の気づき",
                ),
            ]
        )
        person = db.session.get(Talent, talent_id)
        assert person is not None
        person.invoice_registration = "T1234567890123"
        place = db.session.get(Office, 1)
        assert place is not None
        # 架空の住所と、海の上の位置。
        place.address, place.lat_e6, place.lon_e6 = (
            "架空市架空町1-1",
            10_000_000,
            150_000_000,
        )
        db.session.commit()
    csrf = token(office, office_path)
    reduction = {
        "csrf_token": csrf,
        "month": month,
        "category": "collect",
        "before": "30",
        "after": "10",
    }
    assert office.post(office_path + "/reductions", data=reduction).status_code == 302
    with app.app_context():
        reduction_id = db.session.scalar(select(Reduction.id))

    # 進行中の伴走と、確定していない明細がある間は、どちらも退会できない。
    for client in (talent, office):
        page = client.get(WITHDRAW).get_data(as_text=True)
        assert "進行中の伴走があります" in page and 'name="password"' not in page
        assert _leave(client) == 409
    assert _end(office, office_path) == 302
    for client in (talent, office):
        page = client.get(WITHDRAW).get_data(as_text=True)
        assert "進行中の伴走" not in page and "確定していない明細があります" in page
        assert _leave(client) == 409
    assert _user(app, 2)[2] and _user(app, 1)[2]

    # 終わった伴走でも、減った時間の確認と明細の確定は続けられる。
    for action in (
        f"reductions/{reduction_id}/confirm",
        f"statements/{month}/finalize",
    ):
        assert (
            office.post(
                f"{office_path}/{action}", data={"csrf_token": csrf}
            ).status_code
            == 302
        )
    with app.app_context():
        bill = db.session.scalar(select(Statement))
        assert bill is not None and bill.status == StatementStatus.FINALIZED
        assert bill.invoice_registration == "T1234567890123"

    # パスワードと確認の印がそろわなければ、何も変わらない。
    assert _leave(talent, "wrong passphrase here") == 422
    assert _leave(talent, confirm="") == 422
    assert _user(app, 2) == ("tsunagite@example.org", "架空候補0", True)

    response = talent.post(
        WITHDRAW,
        data={
            "csrf_token": token(talent, "/account"),
            "password": PASSWORD,
            "confirm": "yes",
            # 画面から相手を指しても、退会するのはログイン中の本人だけ。
            "user_id": "1",
        },
    )
    assert response.status_code == 200
    assert "退会しました" in response.get_data(as_text=True)
    assert _user(app, 2) == ("withdrawn-2@withdrawn.invalid", "退会した人", False)
    assert _user(app, 1)[2]
    # ログインは切れ、同じメールアドレスでも入れない。
    assert talent.get("/t/").status_code == 302
    assert sign_in(app.test_client(), "tsunagite@example.org") == 401
    with app.app_context():
        person = db.session.get(Talent, talent_id)
        # 伴走が指している行は残し、中身を消す。
        assert person is not None and person.invoice_registration is None
        assert not person.is_listed and person.translation_verified_at is None
        for kind in (TalentSkill, TrustItem):
            count = db.session.scalar(
                select(func.count())
                .select_from(kind)
                .where(kind.talent_id == talent_id)
            )
            assert count == 0
        bill = db.session.scalar(select(Statement))
        # 月・時間・金額は相手の記録なので残る。振込先と登録番号は消える。
        assert bill is not None and bill.fee_yen == 32000
        assert bill.invoice_registration is None and bill.payout_cipher is None
        assert ledger_failures() == []
        assert db.session.scalar(
            select(AuditLog.id).where(AuditLog.action == "account_withdrawn")
        )
    page = office.get(office_path).get_data(as_text=True)
    assert "架空候補0" not in page and "32,000円" in page

    # 代表がただ1人なら、事務所も閉じる。本文とメモを消し、金額と台帳は残す。
    page = office.get(WITHDRAW).get_data(as_text=True)
    assert "事務所は閉じます" in page and 'name="password"' in page
    assert _leave(office) == 200
    assert _user(app, 1) == ("withdrawn-1@withdrawn.invalid", "退会した人", False)
    with app.app_context():
        closed = db.session.get(Office, 1)
        assert closed is not None and not closed.is_active
        assert (closed.address, closed.lat_e6, closed.lon_e6) == (None, None, None)
        need = db.session.scalar(select(Need))
        assert need is not None and "機密の本文" not in need.body
        visit = db.session.scalar(select(Visit))
        assert visit is not None and visit.note is None and visit.learned_term is None
        assert visit.idea is None
        assert db.session.scalar(select(Statement.fee_yen)) == 32000
        assert db.session.scalar(select(func.count()).select_from(OfficeMember)) == 0
        assert ledger_failures() == []
    admin = app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    listing = admin.get("/admin/").get_data(as_text=True)
    assert listing.count("退会済み") == 2
    assert admin.get("/o/1/").status_code == 404


def test_t54_who_can_withdraw(identity_app: Flask) -> None:
    app = identity_app
    anonymous = app.test_client()
    assert anonymous.get(WITHDRAW).headers["Location"].startswith("/login?")
    # 運営は、この画面から退会できない。
    admin = app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    assert admin.get(WITHDRAW).status_code == 403
    assert _leave(admin) == 403
    assert _user(app, 4)[2]

    with app.app_context():
        staff = User(email="staff@example.org", role=Role.OFFICE, display_name="架空")
        staff.set_password(PASSWORD)
        shared = User(
            email="demo-shared@example.org",
            role=Role.TSUNAGITE,
            display_name="架空",
            is_demo=True,
        )
        shared.set_password(PASSWORD)
        db.session.add_all([staff, shared])
        db.session.flush()
        db.session.add(
            OfficeMember(user_id=staff.id, office_id=1, member_role=MemberRole.STAFF)
        )
        db.session.commit()
        staff_id, shared_id = staff.id, shared.id

    # ほかの人がいる事務所の、ただ1人の代表は、先に運営へ。
    owner = app.test_client()
    assert sign_in(owner, "office@example.org") == 302
    assert "ほかの人のアカウントがあります" in owner.get(WITHDRAW).get_data(
        as_text=True
    )
    assert _leave(owner) == 409 and _user(app, 1)[2]
    # 職員は、自分だけが抜ける。事務所は残る。
    member = app.test_client()
    assert sign_in(member, "staff@example.org") == 302
    assert "事務所は閉じます" not in member.get(WITHDRAW).get_data(as_text=True)
    assert _leave(member) == 200
    assert _user(app, staff_id)[1] == "退会した人"
    assert owner.get("/o/1/").status_code == 200
    # 自治体の担当は、担当の結びつきが消える。
    town = app.test_client()
    assert sign_in(town, "municipality@example.org") == 302
    assert _leave(town) == 200
    with app.app_context():
        assert db.session.scalar(select(MunicipalityMember)) is None
        office = db.session.get(Office, 1)
        assert office is not None and office.is_active

    # デモのアカウントは、みんなで使うので消さない。
    with app.test_request_context():
        with pytest.raises(WithdrawalBlocked, match="デモのアカウント"):
            withdraw(shared_id, PASSWORD, confirmed=True)
    assert _user(app, shared_id)[2]


def test_t54_wrong_passwords_are_rate_limited(identity_app: Flask) -> None:
    client = identity_app.test_client()
    assert sign_in(client, "tsunagite@example.org") == 302
    for _ in range(5):
        assert _leave(client, "wrong passphrase here") == 422
    assert _leave(client) == 429
    assert _user(identity_app, 2)[2]


def test_t54_audit_ip_marks_expire_after_180_days(identity_app: Flask) -> None:
    with identity_app.app_context():
        for days in (181, 179):
            db.session.add(
                AuditLog(
                    action=f"架空{days}",
                    ip_hash="a" * 64,
                    created_at=utc_now() - timedelta(days=days),
                )
            )
        db.session.commit()
    result = identity_app.test_cli_runner().invoke(args=["prune-audit"])
    assert result.exit_code == 0 and "1件" in result.output
    with identity_app.app_context():
        marks = dict(
            db.session.execute(select(AuditLog.action, AuditLog.ip_hash)).all()
        )
        assert marks == {"架空181": None, "架空179": "a" * 64}


@pytest.mark.parametrize(
    ("path", "words"),
    [
        ("/privacy", ("この文面は案です", "顧問先の帳簿・名前・金額は預かりません")),
        ("/terms", ("この文面は案です", "税理士業務", "紹介料・仲介手数料は0円")),
    ],
)
def test_t54_policy_drafts_are_public(
    identity_app: Flask, path: str, words: tuple[str, ...]
) -> None:
    page = identity_app.test_client().get(path)
    assert page.status_code == 200
    for word in words:
        assert word in page.get_data(as_text=True)
    home = identity_app.test_client().get("/").get_data(as_text=True)
    assert 'href="/privacy"' in home and 'href="/terms"' in home
