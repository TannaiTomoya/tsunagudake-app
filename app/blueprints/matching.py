import json
from datetime import date
from typing import cast

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import IntegerField, SelectField  # type: ignore[import-untyped]
from wtforms.fields.choices import SelectChoice  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, NumberRange, Optional  # type: ignore[import-untyped]

from app.authz import (
    require_need,
    require_need_office,
    require_office,
    require_office_engagement,
    require_talent_engagement,
)
from app.blueprints.needs import CATEGORIES, WEEKDAYS, weekday_options
from app.blueprints.statements import RETURNED_KINDS, activity_for
from app.blueprints.talent import TOOLS
from app.extensions import db
from app.models import (
    CandidateStatus,
    Engagement,
    EngagementStatus,
    MatchCandidate,
    MatchRun,
    MemberRole,
    Municipality,
    Need,
    NeedStatus,
    NeedTranslation,
    Office,
    OfficeMember,
    Role,
    Talent,
    TalentAffiliation,
    TalentSkill,
    TalentSkillKind,
    User,
    today_in_japan,
    utc_now,
)
from app.services.checkin import (
    ETA_CHOICES,
    CheckinConflict,
    CheckinError,
    arrivals,
    arrive,
    checkin_on,
    head_out,
    japan_clock,
    uses_location,
)
from app.services.checks import CheckConflict, CheckError, save_summary, totals_for
from app.services.engagement import END_REASONS, EngagementConflict, end_engagement
from app.services.ledger import records_for_need
from app.services.matching import (
    MAX_ESTIMATE_HOURS,
    MatchConflict,
    Offer,
    cancel,
    generate,
    invite,
    latest_run,
    respond,
)
from app.services.messages import unread_count
from app.services.office import ACCOUNTING_SOFTWARE
from app.services.trust import TrustConflict, verify_skill
from tsunagu_rules.distance import NEAR_RADIUS_M
from tsunagu_rules.value import estimate_value

bp = Blueprint("matching", __name__)
EXCLUSIONS = {
    "pledge": "守秘の誓約が未有効",
    "training": "研修が未有効",
    "translation": "翻訳課題が未確認",
    "distance": "移動時間が60分超または不明",
    "weekday": "希望曜日が合わない",
    "capacity": "受け持ち上限",
    "unlisted": "候補への掲載がオフ",
    "declined": "この困りごとを辞退済み",
}
AFFILIATIONS = {
    TalentAffiliation.KYORYOKUTAI: "地域おこし協力隊",
    TalentAffiliation.GRADUATE: "修了生",
    TalentAffiliation.OTHER: "その他",
    TalentAffiliation.NONE: "—",
}
CANDIDATE_LABELS = {
    "listed": "候補",
    "invited": "回答待ち",
    "accepted": "合意済み",
    "declined": "辞退",
    "closed": "終了",
    "expired": "期限切れ",
}


class OfferForm(FlaskForm):  # type: ignore[misc]
    weekday = SelectField(
        "曜日",
        choices=[SelectChoice(str(day), label) for day, label in WEEKDAYS],
        coerce=int,
        validators=[InputRequired()],
    )
    share_percent = IntegerField(
        "料金の割合（％）",
        default=40,
        validators=[InputRequired(), NumberRange(min=1, max=49)],
    )
    estimate_hours = IntegerField(
        "減る時間の見込み", validators=[Optional(), NumberRange(0, MAX_ESTIMATE_HOURS)]
    )
    cap_yen = IntegerField(
        "月の上限（円、省略可）",
        validators=[Optional(), NumberRange(min=0, max=2147483647)],
    )


def _actor() -> User:
    return cast(User, current_user)


def _category_labels(need_id: int) -> list[str]:
    selected = set(
        db.session.scalars(
            select(NeedTranslation.category).where(NeedTranslation.need_id == need_id)
        )
    )
    return [label for kind, label in CATEGORIES.items() if kind in selected]


def _candidate_page(
    office: Office,
    need_id: int,
    error: str | None = None,
    *,
    candidate_id: int | None = None,
    submitted: OfferForm | None = None,
) -> str:
    need = require_need(office, need_id)
    run = latest_run(need)
    candidates = []
    if run:
        query = (
            select(MatchCandidate, Talent, User.display_name, Municipality.name)
            .join(Talent, Talent.id == MatchCandidate.talent_id)
            .join(User, User.id == Talent.user_id)
            .join(Municipality, Municipality.id == Talent.municipality_id)
            .where(MatchCandidate.match_id == run.id)
            .order_by(MatchCandidate.rank)
        )
        candidates = list(db.session.execute(query))
    verified_tools: dict[int, list[str]] = {}
    if candidates:
        skills = db.session.scalars(
            select(TalentSkill)
            .where(
                TalentSkill.talent_id.in_(
                    [talent.id for _, talent, _, _ in candidates]
                ),
                TalentSkill.kind == TalentSkillKind.TOOL,
                TalentSkill.verified_at.is_not(None),
            )
            .order_by(TalentSkill.tag)
        )
        for skill in skills:
            verified_tools.setdefault(skill.talent_id, []).append(
                TOOLS.get(skill.tag, skill.tag)
            )
    suggested_hours = estimate_value(
        need.monthly_hours, office.hourly_rate_yen
    ).projected_hours[1]
    user = _actor()
    member_role = db.session.scalar(
        select(OfficeMember.member_role).where(
            OfficeMember.office_id == office.id,
            OfficeMember.user_id == user.id,
        )
    )
    return render_template(
        "matching/candidates.html",
        office=office,
        need=need,
        run=run,
        candidates=candidates,
        writable=user.role == Role.OFFICE and need.status == NeedStatus.PUBLISHED,
        owner=user.role == Role.OFFICE and member_role == MemberRole.OWNER,
        offer_form=OfferForm(
            formdata=None, data={"estimate_hours": suggested_hours or None}
        ),
        suggested_hours=suggested_hours,
        max_estimate_hours=MAX_ESTIMATE_HOURS,
        estimate_categories=_category_labels(need_id),
        software_name=ACCOUNTING_SOFTWARE.get(
            office.software or "", office.software or "未設定"
        ),
        verified_tools=verified_tools,
        submitted=submitted,
        selected_candidate_id=candidate_id,
        offer_days={
            candidate.id: weekday_options(need.weekdays_mask & talent.weekdays_mask)
            for candidate, talent, _, _ in candidates
        },
        exclusions=EXCLUSIONS,
        labels=CANDIDATE_LABELS,
        affiliations=AFFILIATIONS,
        pending=any(row[0].status == CandidateStatus.INVITED for row in candidates),
        error=error,
    )


@bp.get("/o/<int:office_id>/needs/<int:need_id>/candidates")
@login_required  # type: ignore[untyped-decorator]
def candidates(office_id: int, need_id: int) -> str:
    return _candidate_page(require_office(office_id), need_id)


@bp.post("/o/<int:office_id>/needs/<int:need_id>/candidates")
@login_required  # type: ignore[untyped-decorator]
def run_match(office_id: int, need_id: int) -> ResponseReturnValue:
    office = require_need_office(office_id, need_id)
    try:
        generate(office, need_id, _actor().id)
    except MatchConflict as exc:
        return _candidate_page(office, need_id, str(exc)), 409
    return redirect(
        url_for("matching.candidates", office_id=office_id, need_id=need_id)
    )


@bp.post("/o/<int:office_id>/needs/<int:need_id>/candidates/<int:candidate_id>/invite")
@bp.post(
    "/o/<int:office_id>/needs/<int:need_id>/candidates/<int:candidate_id>/cancel",
    endpoint="cancel_invitation",
)
@login_required  # type: ignore[untyped-decorator]
def invitation(office_id: int, need_id: int, candidate_id: int) -> ResponseReturnValue:
    office = require_need_office(office_id, need_id, owner_only=True)
    form = None
    try:
        if request.path.endswith("/cancel"):
            cancel(office, need_id, candidate_id, _actor().id)
        else:
            form = OfferForm()
            if not form.validate_on_submit():
                return _candidate_page(
                    office,
                    need_id,
                    "料金の割合は1〜49の整数で入力してください。"
                    if form.share_percent.errors
                    else f"見込み時間は0〜{MAX_ESTIMATE_HOURS}の整数です。"
                    if form.estimate_hours.errors
                    else "曜日と月の上限額（円）を確認してください。",
                    candidate_id=candidate_id,
                    submitted=form,
                ), 422
            invite(
                office,
                need_id,
                candidate_id,
                _actor().id,
                Offer(
                    form.weekday.data,
                    form.share_percent.data * 100,
                    form.cap_yen.data,
                    form.estimate_hours.data,
                ),
            )
    except MatchConflict as exc:
        return _candidate_page(
            office, need_id, str(exc), candidate_id=candidate_id, submitted=form
        ), 409
    return redirect(
        url_for("matching.candidates", office_id=office_id, need_id=need_id)
    )


def talent_activity(
    user_id: int,
) -> tuple[list[tuple[MatchCandidate, str, list[str]]], list[tuple[Engagement, str]]]:
    invitations = (
        select(MatchCandidate, Office.name, Need.id)
        .join(Talent, Talent.id == MatchCandidate.talent_id)
        .join(MatchRun, MatchRun.id == MatchCandidate.match_id)
        .join(Need, Need.id == MatchRun.need_id)
        .join(Office, Office.id == Need.office_id)
        .where(
            Talent.user_id == user_id,
            MatchCandidate.status == CandidateStatus.INVITED,
            Need.status == NeedStatus.PUBLISHED,
            Office.is_active.is_(True),
        )
        .order_by(MatchCandidate.id)
    )
    engagements = (
        select(Engagement, Office.name)
        .join(Talent, Talent.id == Engagement.talent_id)
        .join(Office, Office.id == Engagement.office_id)
        .where(Talent.user_id == user_id)
        .order_by(Engagement.id)
    )
    offers = [
        (candidate, name, _category_labels(need_id))
        for candidate, name, need_id in db.session.execute(invitations)
    ]
    return offers, [
        (engagement, name) for engagement, name in db.session.execute(engagements)
    ]


@bp.post("/t/invitations/<int:candidate_id>/<any(accept,decline):answer>")
@login_required  # type: ignore[untyped-decorator]
def answer_invitation(candidate_id: int, answer: str) -> ResponseReturnValue:
    user = _actor()
    if user.role != Role.TSUNAGITE:
        abort(403 if user.role == Role.ADMIN else 404)
    try:
        engagement_id = respond(candidate_id, user.id, accept=answer == "accept")
    except MatchConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    if engagement_id is not None:
        return redirect(
            url_for("matching.talent_engagement", engagement_id=engagement_id)
        )
    return redirect(url_for("auth.talent_home"))


def _day_label(day: date) -> str:
    return f"{day.month}月{day.day}日({'月火水木金土日'[day.weekday()]})"


def _engagement_page(
    engagement: Engagement,
    *,
    office_side: bool,
    check_error: str | None = None,
    checkin_error: str | None = None,
) -> str:
    need = db.session.scalar(
        select(Need).where(
            Need.id == engagement.need_id,
            Need.office_id == engagement.office_id,
        )
    )
    office = db.session.get(Office, engagement.office_id)
    if need is None or office is None:
        abort(404)
    today = today_in_japan()
    checkin = checkin_on(engagement.id, today)
    ended = engagement.status == EngagementStatus.ENDED
    upcoming = engagement.next_visit_on
    activity = activity_for(engagement, office_side=office_side)
    estimate = None
    if activity.owner or activity.talent_writer:
        estimate = db.session.execute(
            select(MatchCandidate, User.display_name)
            .join(Talent, Talent.id == MatchCandidate.talent_id)
            .join(User, User.id == Talent.user_id)
            .where(
                MatchCandidate.id == engagement.candidate_id,
                MatchCandidate.talent_id == engagement.talent_id,
                MatchCandidate.estimate_total.is_not(None),
            )
        ).one_or_none()
    return render_template(
        "matching/engagement.html",
        engagement=engagement,
        need=need,
        weekdays=dict(WEEKDAYS),
        skills=db.session.scalars(
            select(TalentSkill)
            .where(TalentSkill.talent_id == engagement.talent_id)
            .order_by(TalentSkill.kind, TalentSkill.tag)
        ).all(),
        tools=TOOLS,
        activity=activity,
        sent_estimate=estimate,
        office_name=office.name,
        estimate_categories=_category_labels(need.id) if estimate else [],
        categories=CATEGORIES,
        returned_kinds=RETURNED_KINDS,
        end_reasons=END_REASONS,
        checks=totals_for(engagement.id),
        check_error=check_error,
        checkin=checkin,
        checkin_error=checkin_error,
        checkin_times={
            name: japan_clock(moment)
            for name, moment in (
                ("heading", checkin.heading_at if checkin else None),
                ("eta", checkin.eta_at if checkin else None),
                ("arrived", checkin.arrived_at if checkin else None),
            )
            if moment is not None
        },
        eta_choices=ETA_CHOICES,
        uses_location=uses_location(office),
        near_radius=NEAR_RADIUS_M,
        arrivals=arrivals(engagement.id),
        # 住所は、合意したあとの担当つなぎ手にだけ。終わった伴走では見せない。
        office_address=office.address if not office_side and not ended else None,
        next_visit=_day_label(upcoming) if upcoming and upcoming >= today else None,
        # 画面に出すのは、読める人（事務所の代表・職員、担当つなぎ手）にだけ。
        unread_messages=unread_count(engagement.id, office_side=office_side),
        ledger=[
            (row, json.loads(row.payload)["data"]) for row in records_for_need(need)
        ],
    )


@bp.get("/o/<int:office_id>/engagements/<int:engagement_id>")
@login_required  # type: ignore[untyped-decorator]
def office_engagement(office_id: int, engagement_id: int) -> str:
    return _engagement_page(
        require_office_engagement(office_id, engagement_id), office_side=True
    )


@bp.get("/t/engagements/<int:engagement_id>")
@login_required  # type: ignore[untyped-decorator]
def talent_engagement(engagement_id: int) -> str:
    return _engagement_page(require_talent_engagement(engagement_id), office_side=False)


@bp.post(
    "/o/<int:office_id>/engagements/<int:engagement_id>/skills/<int:skill_id>/verify"
)
@login_required  # type: ignore[untyped-decorator]
def skill_confirmation(
    office_id: int, engagement_id: int, skill_id: int
) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, write=True)
    try:
        verify_skill(engagement, skill_id, _actor().id, request.form.get("tag", ""))
    except TrustConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    return redirect(
        url_for(
            "matching.office_engagement",
            office_id=office_id,
            engagement_id=engagement_id,
            _anchor="skills",
        )
    )


@bp.post("/t/engagements/<int:engagement_id>/checks")
@login_required  # type: ignore[untyped-decorator]
def check_summary(engagement_id: int) -> ResponseReturnValue:
    # 件数を記録できるのは、担当するつなぎ手だけ。受け取るのは3つの整数だけ。
    engagement = require_talent_engagement(engagement_id, write=True)
    try:
        save_summary(
            engagement,
            _actor().id,
            request.form.get("compared"),
            request.form.get("flagged"),
            request.form.get("uncomparable"),
            today_in_japan(),
        )
    except CheckError as exc:
        page = _engagement_page(engagement, office_side=False, check_error=str(exc))
        return page, 422
    except CheckConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    return redirect(
        url_for(
            "matching.talent_engagement", engagement_id=engagement_id, _anchor="checks"
        )
    )


@bp.post("/t/engagements/<int:engagement_id>/checkins")
@login_required  # type: ignore[untyped-decorator]
def checkin(engagement_id: int) -> ResponseReturnValue:
    # 押せるのは、担当するつなぎ手だけ。日付と時刻は、サーバーの時計から決める。
    engagement = require_talent_engagement(engagement_id, write=True)
    action = request.form.get("action", "")
    if action not in {"heading", "arrived"}:
        abort(400)
    try:
        if action == "heading":
            head_out(
                engagement,
                _actor().id,
                request.form.get("minutes"),
                today_in_japan(),
                utc_now(),
            )
        else:
            # 受け取った位置は、比べるためにだけ使い、保存も記録もしない。
            arrive(
                engagement,
                _actor().id,
                request.form.get("lat"),
                request.form.get("lon"),
                today_in_japan(),
                utc_now(),
                consent=request.form.get("consent") == "yes",
            )
    except CheckinError as exc:
        page = _engagement_page(engagement, office_side=False, checkin_error=str(exc))
        return page, 422
    except CheckinConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    return redirect(
        url_for(
            "matching.talent_engagement", engagement_id=engagement_id, _anchor="checkin"
        )
    )


@bp.post("/o/<int:office_id>/engagements/<int:engagement_id>/end")
@bp.post("/t/engagements/<int:engagement_id>/end", endpoint="talent_end")
@login_required  # type: ignore[untyped-decorator]
def end(engagement_id: int, office_id: int | None = None) -> ResponseReturnValue:
    # 終えられるのは、事務所の代表と、担当するつなぎ手本人だけ。
    if office_id is not None:
        engagement = require_office_engagement(
            office_id, engagement_id, owner_only=True
        )
    else:
        engagement = require_talent_engagement(engagement_id, write=True)
    try:
        end_engagement(
            engagement,
            _actor().id,
            request.form.get("reason", ""),
            by_office=office_id is not None,
        )
    except EngagementConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    if office_id is not None:
        return redirect(
            url_for(
                "matching.office_engagement",
                office_id=office_id,
                engagement_id=engagement_id,
            )
        )
    return redirect(url_for("matching.talent_engagement", engagement_id=engagement_id))
