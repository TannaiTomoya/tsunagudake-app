from datetime import UTC, datetime
from typing import cast
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from sqlalchemy import and_, case, select
from werkzeug.wrappers import Response
from wtforms import EmailField, PasswordField  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length  # type: ignore[import-untyped]

from app.authz import require_municipality, require_office
from app.blueprints.matching import talent_activity
from app.blueprints.needs import CATEGORIES, STATUSES, WEEKDAYS, weekday_options
from app.blueprints.statements import RETURNED_KINDS
from app.blueprints.talent import TOOLS
from app.charts import region_figures
from app.extensions import db
from app.models import (
    Engagement,
    MatchCandidate,
    MatchRun,
    MemberRole,
    Municipality,
    MunicipalityMember,
    Need,
    NeedStatus,
    Office,
    OfficeMember,
    Reduction,
    ReductionStatus,
    Role,
    Talent,
    TalentSkill,
    User,
    today_in_japan,
)
from app.services import second_factor
from app.services.auth import authenticate, requires_second_factor
from app.services.municipalities import chosen_prefecture, prefectures, towns_in
from app.services.office import (
    OfficeNotFound,
    OfficeProfileError,
    save_client_count,
)
from app.services.region import dashboard_for
from app.services.routes import (
    RouteConflict,
    RouteError,
    RouteOfficeNotFound,
    routes_for,
    save_route,
)
from app.services.statement import confirmed_visits, expected_income
from app.services.tasks import tasks_for
from app.services.trust import trust_statuses
from tsunagu_rules import RULES_VERSION
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
    if user.role == Role.ADMIN:
        return url_for("admin.index")
    return url_for("auth.account")


def start_session(user: User) -> Response:
    session.clear()
    if requires_second_factor(user):
        second_factor.begin(user)
        return redirect(url_for("auth.challenge"))
    session.permanent = True
    login_user(user)
    return redirect(_destination(user))


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
        return start_session(user)
    return render_template("auth/login.html", form=form)


@bp.route("/login/second-factor", methods=["GET", "POST"])
def challenge() -> Response:
    user, factor = second_factor.pending(submitted=request.method == "POST")
    if user is None or factor is None:
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        accepted, codes = second_factor.confirm(
            user, factor, request.form.get("code", "")
        )
        if accepted:
            session.clear()
            session.permanent = True
            session["second_factor_identity"] = user.get_id()
            login_user(user)
            if not codes:
                return redirect(_destination(user))
            response = make_response(
                render_template(
                    "auth/recovery.html", codes=codes, workspace_url=_destination(user)
                )
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        error = "コードを確認してください。"
    response = make_response(
        render_template(
            "auth/second_factor.html",
            setup_key=second_factor.setup_key(factor),
            error=error,
        ),
        401 if error else 200,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/logout")
@login_required  # type: ignore[untyped-decorator]
def logout() -> Response:
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.get("/account/today")
@login_required  # type: ignore[untyped-decorator]
def today_tasks() -> str:
    actor = cast(User, current_user)
    return render_template(
        "auth/today.html", tasks=tasks_for(actor), workspace_url=_destination(actor)
    )


@bp.get("/account")
@login_required  # type: ignore[untyped-decorator]
def account() -> str:
    return render_template(
        "auth/home.html",
        heading="マイページ",
        workspace_url=_destination(cast(User, current_user)),
    )


@bp.get("/o/<int:office_id>/")
@login_required  # type: ignore[untyped-decorator]
def office_home(office_id: int) -> str:
    office = require_office(office_id)
    confirmed_hours = (
        select(Reduction.id)
        .where(
            Reduction.engagement_id == Engagement.id,
            Reduction.month == today_in_japan().strftime("%Y-%m"),
            Reduction.status == ReductionStatus.CONFIRMED,
        )
        .exists()
        .label("confirmed_hours")
    )
    status_order = case(
        {
            NeedStatus.ENGAGED: 0,
            NeedStatus.PUBLISHED: 1,
            NeedStatus.DRAFT: 2,
            NeedStatus.BLOCKED: 3,
        },
        value=Need.status,
        else_=4,
    )
    items = db.session.execute(
        select(Need, Engagement.id, confirmed_hours)
        .outerjoin(
            Engagement,
            and_(Engagement.need_id == Need.id, Engagement.office_id == office.id),
        )
        .where(Need.office_id == office.id)
        .order_by(status_order, confirmed_hours.desc(), Need.id.desc())
    ).all()
    return render_template(
        "needs/office.html",
        heading=office.name,
        office=office,
        items=items,
        weekday_options=weekday_options,
        statuses=STATUSES,
        can_write=current_user.role == Role.OFFICE,
    )


@bp.route("/o/<int:office_id>/profile", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def office_profile(office_id: int) -> ResponseReturnValue:
    user = cast(User, current_user)
    error = None
    if request.method == "POST":
        # 直せるのは代表だけ。事務所は URL の所属から決め、画面からは数だけを受け取る。
        office = require_office(office_id, owner_only=True)
        try:
            save_client_count(office.id, user.id, request.form.get("client_count"))
        except OfficeNotFound:
            abort(404)
        except OfficeProfileError as exc:
            error = str(exc)
        else:
            return redirect(url_for("auth.office_profile", office_id=office.id))
    else:
        office = require_office(office_id)
    owner = (
        db.session.scalar(
            select(OfficeMember.member_role).where(
                OfficeMember.office_id == office.id, OfficeMember.user_id == user.id
            )
        )
        == MemberRole.OWNER
    )
    town = db.session.get(Municipality, office.municipality_id)
    return (
        render_template(
            "auth/office_profile.html",
            heading=office.name,
            office=office,
            town=town.name if town else "",
            owner=owner,
            error=error,
            entered=request.form.get("client_count", "") if error else None,
        ),
        422 if error else 200,
    )


def _japan_day(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(ZoneInfo("Asia/Tokyo"))
    return f"{local.month}月{local.day}日({'月火水木金土日'[local.weekday()]})"


@bp.route("/o/<int:office_id>/routes", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def office_routes(office_id: int) -> ResponseReturnValue:
    user = cast(User, current_user)
    error = None
    status = 200
    if request.method == "POST":
        # 登録できるのは代表だけ。事務所は URL から、確かめた人はログインから決める。
        office = require_office(office_id, owner_only=True)
        try:
            save_route(
                office.id,
                user.id,
                request.form.get("municipality", type=int),
                request.form.get("minutes"),
            )
        except RouteOfficeNotFound:
            abort(404)
        except RouteError as exc:
            error, status = str(exc), 422
        except RouteConflict as exc:
            error, status = str(exc), 409
        else:
            return redirect(url_for("auth.office_routes", office_id=office.id))
    else:
        office = require_office(office_id)
    owner = (
        db.session.scalar(
            select(OfficeMember.member_role).where(
                OfficeMember.office_id == office.id, OfficeMember.user_id == user.id
            )
        )
        == MemberRole.OWNER
    )
    # 通常の事務所が選べるのは、実在の町だけ。デモの事務所は登録しない。
    groups = [] if office.is_demo else prefectures(demo=False)
    prefecture = chosen_prefecture(
        groups, request.args.get("prefecture"), office.municipality_id
    )
    return (
        render_template(
            "auth/office_routes.html",
            heading=office.name,
            office=office,
            owner=owner,
            error=error,
            routes=[(row, _japan_day(row.confirmed_at)) for row in routes_for(office)],
            prefectures=groups,
            prefecture=prefecture,
            towns=towns_in(prefecture, demo=False) if groups else [],
            chosen=request.form.get("municipality", type=int),
            entered=request.form.get("minutes", "") if error else "",
            limit=current_app.config.get("TRAVEL_LIMIT_MINUTES", 60),
        ),
        status,
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
        skills=db.session.scalars(
            select(TalentSkill)
            .where(TalentSkill.talent_id == talent.id)
            .order_by(TalentSkill.kind, TalentSkill.tag)
        ).all()
        if talent
        else (),
        score=db.session.scalar(
            select(MatchCandidate)
            .join(MatchRun)
            .where(
                MatchCandidate.talent_id == talent.id,
                MatchRun.rules_version == RULES_VERSION,
            )
            .order_by(MatchCandidate.id.desc())
            .limit(1)
        )
        if talent
        else None,
        tools=TOOLS,
        categories=CATEGORIES,
        expected_income=expected_income(talent.id, today.strftime("%Y-%m"))
        if talent
        else 0,
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
    demo = cast(User, current_user).is_demo
    dashboard = dashboard_for(municipality, threshold, today_in_japan(), demo=demo)
    return render_template(
        "auth/region_home.html",
        heading=municipality.name,
        region_group=municipality.region_group,
        demo_region=demo,
        dashboard=dashboard,
        figures=None
        if dashboard.charts is None
        else region_figures(dashboard.charts, list(RETURNED_KINDS.values())),
    )
