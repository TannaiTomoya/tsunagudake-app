from typing import cast

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import DateField  # type: ignore[import-untyped]
from wtforms.validators import InputRequired  # type: ignore[import-untyped]

from app.blueprints.needs import CATEGORIES, WEEKDAYS
from app.blueprints.talent import AFFILIATIONS, TOOLS
from app.extensions import db
from app.models import (
    BookkeepingLevel,
    Office,
    Role,
    Talent,
    TalentSkill,
    TrustKind,
    User,
    today_in_japan,
)
from app.services.ledger import ledger_failures
from app.services.trust import (
    TrustConflict,
    add_trust_record,
    suspend_user,
    trust_statuses,
    verify_grade,
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
        error=error,
    )


@bp.get("/talents/<int:talent_id>")
@login_required  # type: ignore[untyped-decorator]
def talent(talent_id: int) -> str:
    _admin()
    return _talent_page(talent_id)


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
