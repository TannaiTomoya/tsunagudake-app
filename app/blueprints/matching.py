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
    require_office,
    require_office_engagement,
    require_talent_engagement,
)
from app.blueprints.needs import CATEGORIES, WEEKDAYS
from app.blueprints.statements import activity_for
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
    User,
)
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

bp = Blueprint("matching", __name__)
EXCLUSIONS = {
    "pledge": "守秘の誓約が未有効",
    "training": "研修が未有効",
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
    share_bp = IntegerField(
        "料金の割合（1%＝100、40%なら4000・50%未満）",
        default=4000,
        validators=[InputRequired(), NumberRange(min=1, max=4999)],
    )
    cap_yen = IntegerField(
        "月の上限（円、省略可）",
        validators=[Optional(), NumberRange(min=0, max=2147483647)],
    )


def _actor() -> User:
    return cast(User, current_user)


def _candidate_page(office: Office, need_id: int, error: str | None = None) -> str:
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
    office = require_office(office_id, write=True)
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
    office = require_office(office_id, owner_only=True)
    try:
        if request.path.endswith("/cancel"):
            cancel(office, need_id, candidate_id, _actor().id)
        else:
            form = OfferForm()
            if not form.validate_on_submit():
                return _candidate_page(
                    office, need_id, "曜日と料金の条件を確認してください。"
                ), 422
            invite(
                office,
                need_id,
                candidate_id,
                _actor().id,
                Offer(form.weekday.data, form.share_bp.data, form.cap_yen.data),
            )
    except MatchConflict as exc:
        return _candidate_page(office, need_id, str(exc)), 409
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


def _engagement_page(engagement: Engagement, *, office_side: bool) -> str:
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
        activity=activity_for(engagement, office_side=office_side),
        categories=CATEGORIES,
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
