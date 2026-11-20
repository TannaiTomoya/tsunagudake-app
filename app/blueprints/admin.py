from typing import cast

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import DateField, EmailField, SelectField  # type: ignore[import-untyped]
from wtforms.fields.choices import SelectChoice  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length  # type: ignore[import-untyped]

from app.blueprints.needs import CATEGORIES, WEEKDAYS
from app.blueprints.talent import AFFILIATIONS, TOOLS
from app.extensions import db
from app.models import (
    BookkeepingLevel,
    MemberRole,
    Municipality,
    Office,
    OutboxMail,
    Role,
    Talent,
    TalentSkill,
    TrustKind,
    User,
    today_in_japan,
    utc_now,
)
from app.services.accounts import AccountError, Invite, issue_invitation
from app.services.ledger import ledger_failures
from app.services.mail import MailError, mail_body
from app.services.trust import (
    TRANSLATION_CRITERIA,
    TrustConflict,
    add_trust_record,
    suspend_user,
    trust_statuses,
    verify_grade,
    verify_translation,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


class TrainingForm(FlaskForm):  # type: ignore[misc]
    day = DateField("研修を確認した日", validators=[InputRequired()])


def _admin() -> User:
    actor = cast(User, current_user)
    if actor.role != Role.ADMIN:
        abort(404)
    return actor


def _admin_for_talent_action(talent_id: int) -> User:
    actor = cast(User, current_user)
    if actor.role == Role.TSUNAGITE:
        own = db.session.scalar(
            select(Talent.id).where(
                Talent.id == talent_id,
                Talent.user_id == actor.id,
            )
        )
        if own is not None:
            abort(403)
    return _admin()


@bp.get("/")
@login_required  # type: ignore[untyped-decorator]
def index() -> str:
    _admin()
    folks = db.session.execute(
        select(User, Talent.id)
        .outerjoin(Talent, User.id == Talent.user_id)
        .order_by(User.display_name, User.id)
    ).all()
    offices = db.session.scalars(
        select(Office).where(Office.is_active.is_(True)).order_by(Office.id)
    ).all()
    return render_template("admin/index.html", folks=folks, offices=offices)


def _talent_page(talent_id: int, error: str | None = None) -> str:
    profile = db.session.get(Talent, talent_id)
    if profile is None:
        abort(404)
    user = db.session.get(User, profile.user_id)
    if user is None:
        abort(404)
    skills = db.session.scalars(
        select(TalentSkill)
        .where(TalentSkill.talent_id == talent_id)
        .order_by(TalentSkill.kind, TalentSkill.tag)
    ).all()
    today = today_in_japan()
    return render_template(
        "admin/talent.html",
        profile=profile,
        account=user,
        statuses=trust_statuses(profile, today),
        training=TrainingForm(formdata=None, data={"day": today}),
        weekdays=[
            label for day, label in WEEKDAYS if profile.weekdays_mask & (1 << day)
        ],
        tools=TOOLS,
        categories=CATEGORIES,
        affiliations=AFFILIATIONS,
        skills=skills,
        criteria=TRANSLATION_CRITERIA,
        error=error,
    )


@bp.get("/talents/<int:talent_id>")
@login_required  # type: ignore[untyped-decorator]
def talent(talent_id: int) -> str:
    _admin()
    return _talent_page(talent_id)


@bp.post("/talents/<int:talent_id>/translation")
@login_required  # type: ignore[untyped-decorator]
def translation(talent_id: int) -> ResponseReturnValue:
    actor = _admin_for_talent_action(talent_id)
    try:
        verify_translation(talent_id, actor.id, set(request.form.getlist("criterion")))
    except TrustConflict as exc:
        return _talent_page(talent_id, str(exc)), 422
    return redirect(url_for("admin.talent", talent_id=talent_id))


@bp.post("/talents/<int:talent_id>/verify-grade")
@login_required  # type: ignore[untyped-decorator]
def grade(talent_id: int) -> ResponseReturnValue:
    actor = _admin_for_talent_action(talent_id)
    try:
        expected = BookkeepingLevel(request.form.get("grade", ""))
    except ValueError:
        abort(400)
    try:
        verify_grade(talent_id, expected, actor.id)
    except TrustConflict as exc:
        return _talent_page(talent_id, str(exc)), 409
    return redirect(url_for("admin.talent", talent_id=talent_id))


@bp.post("/talents/<int:talent_id>/training")
@login_required  # type: ignore[untyped-decorator]
def training(talent_id: int) -> ResponseReturnValue:
    actor = _admin_for_talent_action(talent_id)
    form = TrainingForm()
    today = today_in_japan()
    if not form.validate_on_submit() or form.day.data > today:
        return _talent_page(talent_id, "研修の日付をご確認ください。"), 422
    try:
        add_trust_record(talent_id, TrustKind.TRAINING, actor.id, form.day.data)
    except TrustConflict as exc:
        return _talent_page(talent_id, str(exc)), 409
    return redirect(url_for("admin.talent", talent_id=talent_id))


@bp.post("/users/<int:user_id>/suspend")
@login_required  # type: ignore[untyped-decorator]
def suspend(user_id: int) -> ResponseReturnValue:
    actor = _admin()
    try:
        suspend_user(user_id, actor.id)
    except TrustConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    return redirect(url_for("admin.index"))


@bp.get("/ledger")
@login_required  # type: ignore[untyped-decorator]
def ledger() -> str:
    _admin()
    return render_template("admin/ledger.html", failures=ledger_failures())


class InviteForm(FlaskForm):  # type: ignore[misc]
    email = EmailField("メールアドレス", validators=[InputRequired(), Length(max=255)])
    role = SelectField(
        "立場",
        choices=[
            SelectChoice("office", "事務所"),
            SelectChoice("tsunagite", "つなぎ手"),
            SelectChoice("municipality", "自治体担当"),
        ],
    )
    office = SelectField("事務所", coerce=int)
    member_role = SelectField(
        "事務所での所属",
        choices=[
            SelectChoice("staff", "職員"),
            SelectChoice("owner", "代表"),
        ],
    )
    municipality = SelectField("担当する市町村", coerce=int)


@bp.route("/invitations/new", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def invite_account() -> ResponseReturnValue:
    actor = _admin()
    demo = current_app.config["APP_ENV"] == "demo"
    form = InviteForm(data={"office": 0, "municipality": 0})
    offices = db.session.scalars(
        select(Office)
        .where(
            Office.is_active.is_(True),
            Office.is_demo.is_(demo),
        )
        .order_by(Office.id)
    )
    form.office.choices = [
        SelectChoice("0", "選択してください"),
        *(SelectChoice(str(office.id), office.name) for office in offices),
    ]
    towns = db.session.scalars(select(Municipality).order_by(Municipality.code))
    form.municipality.choices = [
        SelectChoice("0", "選択してください"),
        *(SelectChoice(str(town.id), town.name) for town in towns),
    ]
    error = None
    if form.validate_on_submit():
        try:
            issue_invitation(
                Invite(
                    email=form.email.data,
                    role=Role(form.role.data),
                    office_id=form.office.data or None,
                    member_role=MemberRole(form.member_role.data),
                    municipality_id=form.municipality.data or None,
                ),
                actor.id,
            )
            return redirect(url_for("admin.mail"))
        except AccountError as exc:
            error = str(exc)
    elif form.is_submitted():
        error = "招待先の入力を確認してください。"
    return render_template(
        "admin/invite.html", form=form, error=error
    ), 422 if error else 200


@bp.get("/mail")
@login_required  # type: ignore[untyped-decorator]
def mail() -> ResponseReturnValue:
    _admin()
    production = current_app.config["APP_ENV"] == "production"
    query = select(OutboxMail).where(OutboxMail.is_demo.is_(not production))
    rows = []
    now = utc_now()
    for item in db.session.scalars(query.order_by(OutboxMail.id.desc()).limit(50)):
        expired = item.expires_at is not None and item.expires_at <= now
        body = ""
        if not production and not expired:
            try:
                body = mail_body(item)
            except MailError:
                body = "本文を読み取れません。"
        rows.append((item, expired, body))
    return (
        render_template("admin/mail.html", rows=rows, production=production),
        200,
        {"Cache-Control": "no-store"},
    )
