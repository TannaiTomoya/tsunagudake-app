from datetime import date, timedelta

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import PASSWORD, _agree, sign_in, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import (
    AuditLog,
    CandidateStatus,
    Engagement,
    MatchCandidate,
    MatchRun,
    MemberRole,
    Need,
    NeedStatus,
    OfficeMember,
    Role,
    User,
    Visit,
    today_in_japan,
)
from app.services.tasks import tasks_for
from tsunagu_rules.types import NeedKind

IDEA = "架空の気づき：資料の箱を、月ごとに分ける"


def _visit(talent: FlaskClient, path: str, **fields: str) -> int:
    data = {
        "csrf_token": token(talent, path),
        "day": today_in_japan().isoformat(),
        "minutes": "60",
        "category": "collect",
        **fields,
    }
    return talent.post(path, data=data).status_code


def _adopt(client: FlaskClient, office_path: str, visit_id: int, **extra: str) -> int:
    data = {"csrf_token": token(client, "/account/password"), **extra}
    path = f"{office_path}/visits/{visit_id}/adopt-idea"
    return client.post(path, data=data).status_code


def _idea(app: Flask) -> tuple[str | None, int | None, bool]:
    with app.app_context():
        visit = db.session.get(Visit, 1)
        assert visit is not None
        return visit.idea, visit.idea_adopted_by, visit.idea_adopted_at is not None


def _other_office_visit(app: Flask) -> int:
    """事務所2（架空事務所B）の伴走と、メモのある訪問を1つ作る。"""
    with app.app_context():
        mine = db.session.scalar(select(Engagement))
        assert mine is not None
        need = Need(
            office_id=2,
            created_by=1,
            body="架空の困りごと",
            monthly_hours=10,
            weekdays_mask=1,
            status=NeedStatus.ENGAGED,
        )
        db.session.add(need)
        db.session.flush()
        run = MatchRun(
            need_id=need.id,
            rules_version="test",
            input_hash="0" * 64,
            excluded_summary={},
            run_by=1,
        )
        db.session.add(run)
        db.session.flush()
        candidate = MatchCandidate(
            match_id=run.id,
            talent_id=mine.talent_id,
            rank=1,
            score=50,
            reasons=[],
            status=CandidateStatus.ACCEPTED,
        )
        db.session.add(candidate)
        db.session.flush()
        theirs = Engagement(
            office_id=2,
            need_id=need.id,
            talent_id=mine.talent_id,
            candidate_id=candidate.id,
            weekday=0,
            hourly_rate_yen=mine.hourly_rate_yen,
            fee_share_bp=mine.fee_share_bp,
            trial_ends_on=mine.trial_ends_on,
        )
        db.session.add(theirs)
        db.session.flush()
        visit = Visit(
            engagement_id=theirs.id,
            visit_date=today_in_japan(),
            minutes=60,
            category=NeedKind.COLLECT,
            idea="架空の、ほかの事務所のメモ",
        )
        db.session.add(visit)
        db.session.commit()
        return visit.id


def test_t60_idea_is_written_by_the_talent_and_marked_by_the_office(
    database_app: Flask,
) -> None:
    app = database_app
    office, talent, office_path, talent_path = _agree(app)
    new = talent_path + "/visits/new"
    form = talent.get(new).get_data(as_text=True)
    assert 'name="idea"' in form and 'maxlength="100"' in form
    assert 'aria-describedby="visit-idea"' in form
    assert "ひと言で。事務所が「今月のメモ」で読みます。顧問先の名前と金額は" in form
    assert "この月のメモはありません" in office.get(office_path).get_data(as_text=True)

    # 100文字まで。空なら残さない。
    assert _visit(talent, new, idea="あ" * 101) == 422
    assert _visit(talent, new, idea="   ") == 302
    assert _idea(app) == (None, None, False)
    page = office.get(office_path).get_data(as_text=True)
    assert "この月のメモはありません" in page and "adopt-idea" not in page
    # メモの無い訪問には、印を付けられない。
    assert _adopt(office, office_path, 1) == 409

    edit = talent_path + "/visits/1/edit"
    assert _visit(talent, edit, idea=f"  {IDEA}  ") == 302
    assert _idea(app) == (IDEA, None, False)
    # 双方が読める。印を付けるボタンは、事務所の画面にだけ出る。
    assert IDEA in talent.get(talent_path).get_data(as_text=True)
    assert "adopt-idea" not in talent.get(talent_path).get_data(as_text=True)
    page = office.get(office_path).get_data(as_text=True)
    assert IDEA in page and "/visits/1/adopt-idea" in page
    # ほかの月には並ばない。
    assert IDEA not in office.get(office_path + "?month=2001-01").get_data(as_text=True)

    # 担当つなぎ手は、自分のメモに印を付けられない。無い訪問は 404。
    assert _adopt(talent, office_path, 1) == 403
    assert _adopt(office, office_path, 99) == 404
    assert _idea(app) == (IDEA, None, False)
    # ほかの事務所の伴走の訪問は、自分の伴走の道からは届かない（404）。
    other = _other_office_visit(app)
    assert _adopt(office, office_path, other) == 404
    with app.app_context():
        stranger = db.session.get(Visit, other)
        assert stranger is not None and stranger.idea_adopted_at is None
    # 文・付けた人は、画面から受け取らない。2回押しても、記録は1つ。
    extra = {"idea": "書き換え", "idea_adopted_by": "2", "month": "2001-01"}
    assert _adopt(office, office_path, 1, **extra) == 302
    assert _adopt(office, office_path, 1) == 302
    assert _idea(app) == (IDEA, 1, True)
    with app.app_context():
        logs = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "idea_adopted")
        ).all()
        assert [(log.actor_user_id, log.target_id) for log in logs] == [(1, 1)]
    page = office.get(office_path).get_data(as_text=True)
    assert "adopt-idea" not in page
    assert '<span class="state" data-status="confirmed">やってみる</span>' in page

    # 文を書き直すと、印は消える。同じ文のままなら、印は残る。
    assert _visit(talent, edit, idea=IDEA, note="架空の連絡") == 302
    assert _idea(app) == (IDEA, 1, True)
    assert _visit(talent, edit, idea=IDEA + "。") == 302
    assert _idea(app) == (IDEA + "。", None, False)


def test_t60_month_end_task_lists_undecided_ideas(identity_app: Flask) -> None:
    app = identity_app
    office, talent, office_path, talent_path = _agree(app)
    assert _visit(talent, talent_path + "/visits/new", idea=IDEA) == 302
    today = today_in_japan()
    last = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(1)

    def titles(day: date, user_id: int = 1) -> list[str]:
        with app.test_request_context():
            user = db.session.get(User, user_id)
            assert user is not None
            return [task.title for task in tasks_for(user, day)]

    with app.app_context():
        staff = User(email="staff@example.org", role=Role.OFFICE, display_name="架空")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.flush()
        db.session.add(
            OfficeMember(user_id=staff.id, office_id=1, member_role=MemberRole.STAFF)
        )
        visit = db.session.scalar(select(Visit))
        assert visit is not None
        # 月の初めの訪問にして、月末の日付から見る。
        visit.visit_date = today.replace(day=1)
        db.session.commit()
        staff_id = staff.id

    name = "今月のメモを読む"
    # 月の最後の7日間だけ。代表にも職員にも出る。つなぎ手には出ない。
    assert name not in titles(last - timedelta(days=7))
    assert name in titles(last - timedelta(days=6))
    assert name in titles(last) and name in titles(last, staff_id)
    assert name not in titles(last, 2)
    # 次の月には持ち越さない。
    assert name not in titles(last + timedelta(days=27))

    # 職員も印を付けられる。決めたメモは、もう出ない。
    clerk = app.test_client()
    assert sign_in(clerk, "staff@example.org") == 302
    assert _adopt(clerk, office_path, 1) == 302
    assert _idea(app) == (IDEA, staff_id, True)
    assert name not in titles(last)
