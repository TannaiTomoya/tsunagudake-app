from dataclasses import dataclass
from typing import cast

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import DateField, IntegerField, SelectField, StringField, TextAreaField  # type: ignore[import-untyped]
from wtforms.fields.choices import SelectChoice  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length, NumberRange, Optional  # type: ignore[import-untyped]

from app.authz import require_office_engagement, require_talent_engagement
from app.blueprints.needs import CATEGORIES
from app.extensions import db
from app.models import (
    Engagement,
    MemberRole,
    Office,
    OfficeMember,
    Reduction,
    ReturnedKind,
    Role,
    Statement,
    StatementStatus,
    Talent,
    User,
    Visit,
    VisitStatus,
    today_in_japan,
)
from app.services.billing import read_payout
from app.services.statement import (
    ReductionInput,
    StatementConflict,
    VisitInput,
    adopt_idea,
    confirm_reduction,
    fee_preview,
    finalize,
    find_statement,
    find_visit,
    month_ideas,
    month_reductions,
    record_payment,
    review_visit,
    save_reduction,
    save_visit,
    valid_month,
)
from tsunagu_rules.types import Fee, NeedKind

bp = Blueprint("statements", __name__)
_CHOICES = [SelectChoice(kind.value, label) for kind, label in CATEGORIES.items()]
RETURNED_KINDS = {
    ReturnedKind.CLIENT_CONSULT: "顧問先の相談",
    ReturnedKind.NEW_CLIENT: "新しい顧問先の対応",
    ReturnedKind.LEFT_EARLY: "早く帰れた",
    ReturnedKind.OTHER: "その他",
}


class VisitForm(FlaskForm):  # type: ignore[misc]
    day = DateField("訪問日", validators=[InputRequired()])
    minutes = IntegerField(
        "訪問時間（分）", validators=[InputRequired(), NumberRange(15, 600)]
    )
    category = SelectField("業務の型", choices=_CHOICES, validators=[InputRequired()])
    learned_term = StringField("聞き返した用語（任意）", validators=[Length(max=100)])
    note = TextAreaField("事務所に伝えること（任意）", validators=[Length(max=300)])
    idea = StringField("気づいたこと（任意）", validators=[Length(max=100)])
    next_visit = DateField("次の訪問日（任意）", validators=[Optional()])


class ReductionForm(FlaskForm):  # type: ignore[misc]
    month = StringField("対象月", validators=[InputRequired(), Length(min=7, max=7)])
    category = SelectField("業務の型", choices=_CHOICES, validators=[InputRequired()])
    before = IntegerField(
        "改善前（月の時間）", validators=[InputRequired(), NumberRange(0, 400)]
    )
    after = IntegerField(
        "現在（月の時間）", validators=[InputRequired(), NumberRange(0, 400)]
    )


@dataclass
class Activity:
    month: str
    visits: list[Visit]
    reductions: list[Reduction]
    statement: Statement | None
    fee: Fee
    office_writer: bool
    owner: bool
    talent_writer: bool
    reduction_form: ReductionForm
    ideas: list[tuple[str, Visit]]

    @property
    def finalized(self) -> bool:
        return (
            self.statement is not None
            and self.statement.status == StatementStatus.FINALIZED
        )


def _actor() -> User:
    return cast(User, current_user)


def activity_for(engagement: Engagement, *, office_side: bool) -> Activity:
    month = request.args.get("month", today_in_japan().strftime("%Y-%m"))
    if not valid_month(month):
        abort(400)
    user = _actor()
    visits = list(
        db.session.scalars(
            select(Visit)
            .where(
                Visit.engagement_id == engagement.id,
            )
            .order_by(Visit.visit_date.desc())
        )
    )
    rows = month_reductions(engagement, month)
    stored = find_statement(engagement, month)
    office_writer = office_side and user.role == Role.OFFICE
    owner = (
        office_writer
        and db.session.scalar(
            select(OfficeMember.member_role).where(
                OfficeMember.office_id == engagement.office_id,
                OfficeMember.user_id == user.id,
            )
        )
        == MemberRole.OWNER
    )
    calculated = (
        Fee(stored.reduced_hours, stored.value_yen, stored.fee_yen, stored.waived)
        if stored and stored.status == StatementStatus.FINALIZED
        else fee_preview(engagement, rows)
    )
    return Activity(
        month,
        visits,
        rows,
        stored,
        calculated,
        office_writer,
        owner,
        not office_side,
        ReductionForm(formdata=None, data={"month": month}),
        [
            (
                f"{visit.visit_date.month}月{visit.visit_date.day}日"
                f"({'月火水木金土日'[visit.visit_date.weekday()]})",
                visit,
            )
            for visit in month_ideas(engagement.id, month)
        ],
    )


def _office_back(
    engagement: Engagement, month: str | None = None
) -> ResponseReturnValue:
    return redirect(
        url_for(
            "matching.office_engagement",
            office_id=engagement.office_id,
            engagement_id=engagement.id,
            month=month,
        )
    )


def _conflict(exc: StatementConflict) -> ResponseReturnValue:
    return render_template("error.html", message=str(exc)), 409


@bp.route("/t/engagements/<int:engagement_id>/visits/new", methods=["GET", "POST"])
@bp.route(
    "/t/engagements/<int:engagement_id>/visits/<int:visit_id>/edit",
    methods=["GET", "POST"],
)
@login_required  # type: ignore[untyped-decorator]
def visit_form(engagement_id: int, visit_id: int | None = None) -> ResponseReturnValue:
    engagement = require_talent_engagement(engagement_id, write=True)
    initial: dict[str, object] = {"day": today_in_japan()}
    if visit_id is not None:
        visit = find_visit(engagement, visit_id)
        if visit.status != VisitStatus.RECORDED:
            return _conflict(StatementConflict("確認済みの訪問は書き換えられません。"))
        initial = {
            "day": visit.visit_date,
            "minutes": visit.minutes,
            "category": visit.category.value,
            "note": visit.note,
            "learned_term": visit.learned_term,
            "idea": visit.idea,
        }
    form = VisitForm(data=initial)
    error = None
    status = 200
    if form.validate_on_submit():
        try:
            save_visit(
                engagement,
                VisitInput(
                    form.day.data,
                    form.minutes.data,
                    NeedKind(form.category.data),
                    (form.note.data or "").strip() or None,
                    (form.learned_term.data or "").strip() or None,
                    (form.idea.data or "").strip() or None,
                    form.next_visit.data,
                ),
                visit_id,
            )
            return redirect(
                url_for("matching.talent_engagement", engagement_id=engagement.id)
            )
        except StatementConflict as exc:
            error, status = str(exc), 409
    elif form.is_submitted():
        error, status = (
            "日付、業務の型、15〜600分の訪問時間、300文字以内の伝える内容、"
            "100文字以内の気づいたこと、次の訪問日をご確認ください。",
            422,
        )
    return render_template(
        "matching/visit_form.html",
        form=form,
        engagement=engagement,
        visit_id=visit_id,
        error=error,
    ), status


@bp.post(
    "/o/<int:office_id>/engagements/<int:engagement_id>/visits/<int:visit_id>/<any(confirm,dispute):action>"
)
@login_required  # type: ignore[untyped-decorator]
def visit_review(
    office_id: int, engagement_id: int, visit_id: int, action: str
) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, write=True)
    try:
        review_visit(engagement, visit_id, _actor().id, disputed=action == "dispute")
    except StatementConflict as exc:
        return _conflict(exc)
    return _office_back(engagement)


@bp.post(
    "/o/<int:office_id>/engagements/<int:engagement_id>/visits/<int:visit_id>/adopt-idea"
)
@login_required  # type: ignore[untyped-decorator]
def idea_adopt(
    office_id: int, engagement_id: int, visit_id: int
) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, write=True)
    try:
        adopt_idea(engagement, visit_id, _actor().id)
    except StatementConflict as exc:
        return _conflict(exc)
    month = request.form.get("month", "")
    return redirect(
        url_for(
            "matching.office_engagement",
            office_id=engagement.office_id,
            engagement_id=engagement.id,
            month=month if valid_month(month) else None,
            _anchor="ideas",
        )
    )


@bp.post("/o/<int:office_id>/engagements/<int:engagement_id>/reductions")
@login_required  # type: ignore[untyped-decorator]
def reduction_save(office_id: int, engagement_id: int) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, write=True)
    form = ReductionForm()
    if not form.validate_on_submit() or not valid_month(form.month.data):
        return render_template(
            "error.html", message="対象月、業務の型、0〜400の整数時間をご確認ください。"
        ), 422
    try:
        save_reduction(
            engagement,
            ReductionInput(
                form.month.data,
                NeedKind(form.category.data),
                form.before.data,
                form.after.data,
            ),
        )
    except StatementConflict as exc:
        return _conflict(exc)
    return _office_back(engagement, form.month.data)


@bp.post(
    "/o/<int:office_id>/engagements/<int:engagement_id>/reductions/<int:reduction_id>/confirm"
)
@login_required  # type: ignore[untyped-decorator]
def reduction_confirm(
    office_id: int, engagement_id: int, reduction_id: int
) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, owner_only=True)
    try:
        month = confirm_reduction(engagement, reduction_id, _actor().id)
    except StatementConflict as exc:
        return _conflict(exc)
    return _office_back(engagement, month)


@bp.post(
    "/o/<int:office_id>/engagements/<int:engagement_id>/statements/<month>/finalize"
)
@login_required  # type: ignore[untyped-decorator]
def statement_finalize(
    office_id: int, engagement_id: int, month: str
) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, owner_only=True)
    if not valid_month(month):
        abort(400)
    try:
        finalize(
            engagement,
            month,
            _actor().id,
            returned_to=request.form.get("returned_to", ""),
            returned_kind=request.form.get("returned_kind", ""),
            due_date=request.form.get("due_on", ""),
        )
    except StatementConflict as exc:
        return _conflict(exc)
    return _office_back(engagement, month)


@bp.post(
    "/o/<int:office_id>/engagements/<int:engagement_id>/statements/<month>/report-transfer"
)
@login_required  # type: ignore[untyped-decorator]
def report_transfer(
    office_id: int, engagement_id: int, month: str
) -> ResponseReturnValue:
    engagement = require_office_engagement(office_id, engagement_id, owner_only=True)
    if not valid_month(month):
        abort(400)
    try:
        record_payment(engagement, month, _actor().id, receipt=False)
    except StatementConflict as exc:
        return _conflict(exc)
    return _office_back(engagement, month)


@bp.post("/t/engagements/<int:engagement_id>/statements/<month>/confirm-receipt")
@login_required  # type: ignore[untyped-decorator]
def confirm_receipt(engagement_id: int, month: str) -> ResponseReturnValue:
    engagement = require_talent_engagement(engagement_id, write=True)
    if not valid_month(month):
        abort(400)
    try:
        record_payment(engagement, month, _actor().id, receipt=True)
    except StatementConflict as exc:
        return _conflict(exc)
    return redirect(
        url_for("matching.talent_engagement", engagement_id=engagement_id, month=month)
    )


@bp.get("/o/<int:office_id>/engagements/<int:engagement_id>/statements/<month>/invoice")
@bp.get("/t/engagements/<int:engagement_id>/statements/<month>/invoice")
@login_required  # type: ignore[untyped-decorator]
def invoice(
    engagement_id: int, month: str, office_id: int | None = None
) -> ResponseReturnValue:
    if office_id is not None:
        engagement = require_office_engagement(
            office_id, engagement_id, owner_only=True
        )
    else:
        engagement = require_talent_engagement(engagement_id)
    if not valid_month(month):
        abort(400)
    bill = find_statement(engagement, month)
    if not bill or bill.status != StatementStatus.FINALIZED:
        abort(404)
    office = db.session.get(Office, engagement.office_id)
    issuer = db.session.scalar(
        select(User.display_name)
        .join(Talent, Talent.user_id == User.id)
        .where(Talent.id == engagement.talent_id)
    )
    return (
        render_template(
            "matching/invoice.html",
            bill=bill,
            office=office,
            issuer=issuer,
            payout=read_payout(bill.payout_cipher),
        ),
        200,
        {"Cache-Control": "no-store"},
    )
