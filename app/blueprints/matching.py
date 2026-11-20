import json
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
    User,
    today_in_japan,
)
from app.services.checks import CheckConflict, CheckError, save_summary, totals_for
from app.services.engagement import END_REASONS, EngagementConflict, end_engagement
from app.services.ledger import records_for_need
from app.services.matching import (
    MatchConflict,
    Offer,
    cancel,
    generate,
    invite,
    latest_run,
    respond,
)
from app.services.trust import TrustConflict, verify_skill

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
        "減る時間の見込み", validators=[Optional(), NumberRange(0, 400)]
    )
    cap_yen = IntegerField(
        "月の上限（円、省略可）",
        validators=[Optional(), NumberRange(min=0, max=2147483647)],
    )


def _actor() -> User:
    return cast(User, current_user)


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
        offer_form=OfferForm(formdata=None),
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
                    else "曜日と月の上限を確認してください。",
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
    offers = []
    for candidate, name, need_id in db.session.execute(invitations):
        categories = db.session.scalars(
            select(NeedTranslation.category).where(NeedTranslation.need_id == need_id)
        )
        selected = set(categories)
        offers.append(
            (
                candidate,
                name,
                [label for kind, label in CATEGORIES.items() if kind in selected],
            )
        )
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


def _engagement_page(
    engagement: Engagement, *, office_side: bool, check_error: str | None = None
) -> str:
    need = db.session.scalar(
        select(Need).where(
            Need.id == engagement.need_id,
            Need.office_id == engagement.office_id,
        )
    )
    if need is None:
        abort(404)
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
        activity=activity_for(engagement, office_side=office_side),
        categories=CATEGORIES,
        returned_kinds=RETURNED_KINDS,
        end_reasons=END_REASONS,
        checks=totals_for(engagement.id),
        check_error=check_error,
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
