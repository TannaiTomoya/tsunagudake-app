from typing import cast

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from sqlalchemy import select
from werkzeug.wrappers import Response
from wtforms import EmailField, PasswordField  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length  # type: ignore[import-untyped]

from app.authz import require_municipality, require_office
from app.blueprints.matching import talent_activity
from app.blueprints.needs import STATUSES, WEEKDAYS
from app.extensions import db
from app.models import (
    Municipality,
    MunicipalityMember,
    Need,
    Office,
    OfficeMember,
    Role,
    Talent,
    User,
    today_in_japan,
)
from app.services.auth import authenticate
from app.services.region import dashboard_for
from app.services.statement import confirmed_visits
from app.services.trust import trust_statuses
from tsunagu_rules.ledger import REQUIRED_BOUNDARIES

bp = Blueprint("auth", __name__)


class LoginForm(FlaskForm):  # type: ignore[misc]
    email = EmailField("メールアドレス", validators=[InputRequired(), Length(max=255)])
    password = PasswordField(
        "パスワード", validators=[InputRequired(), Length(max=1024)]
    )


def _destination(user: User) -> str:
    if user.role == Role.OFFICE:
        office_id = db.session.scalar(
            db.select(OfficeMember.office_id)
            .join(Office)
            .where(OfficeMember.user_id == user.id, Office.is_active.is_(True))
        )
        if office_id is not None:
            return url_for("auth.office_home", office_id=office_id)
    if user.role == Role.TSUNAGITE:
        return url_for("auth.talent_home")
    if user.role == Role.MUNICIPALITY:
        code = db.session.scalar(
            db.select(Municipality.code)
            .join(MunicipalityMember)
            .where(MunicipalityMember.user_id == user.id)
            .order_by(Municipality.code)
            .limit(1)
        )
        if code is not None:
            return url_for("auth.region_home", code=code)
    return url_for("auth.account")


@bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response | tuple[str, int]:
    form = LoginForm()
    if form.is_submitted():
        user = (
            authenticate(form.email.data or "", form.password.data or "")
            if form.validate()
            else None
        )
        if user is None:
            return render_template("auth/login.html", form=form, failed=True), 401
        session.clear()
        session.permanent = True
        login_user(user)
        return redirect(_destination(user))
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required  # type: ignore[untyped-decorator]
def logout() -> Response:
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.get("/account")
@login_required  # type: ignore[untyped-decorator]
def account() -> str:
    return render_template("auth/home.html", heading="ログインしました")


@bp.get("/o/<int:office_id>/")
@login_required  # type: ignore[untyped-decorator]
def office_home(office_id: int) -> str:
    office = require_office(office_id)
    items = db.session.scalars(
        select(Need).where(Need.office_id == office.id).order_by(Need.id.desc())
    ).all()
    return render_template(
        "needs/office.html",
        heading=office.name,
        office=office,
        items=items,
        statuses=STATUSES,
        can_write=current_user.role == Role.OFFICE,
    )


@bp.get("/t/")
@login_required  # type: ignore[untyped-decorator]
def talent_home() -> str:
    user = cast(User, current_user)
    if user.role != Role.TSUNAGITE:
        abort(404)
    talent = db.session.scalar(select(Talent).where(Talent.user_id == user.id))
    invitations, engagements = talent_activity(user.id)
    today = today_in_japan()
    return render_template(
        "auth/talent_home.html",
        heading="信用台帳",
        talent=talent,
        statuses=trust_statuses(talent, today) if talent is not None else (),
        today=today,
        confirmed_visits=confirmed_visits(talent.id) if talent is not None else 0,
        invitations=invitations,
        weekday_names=dict(WEEKDAYS),
        boundaries=REQUIRED_BOUNDARIES,
        engagements=engagements,
    )


@bp.get("/r/<code>/")
@login_required  # type: ignore[untyped-decorator]
def region_home(code: str) -> str:
    municipality = require_municipality(code)
    threshold = current_app.config["REGION_K_ANONYMITY"]
    return render_template(
        "auth/region_home.html",
        heading=municipality.name,
        region_group=municipality.region_group,
        dashboard=dashboard_for(municipality, threshold),
    )
