import json
from typing import cast

from flask import Blueprint, redirect, render_template, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import IntegerField, SelectField, SelectMultipleField, TextAreaField  # type: ignore[import-untyped]
from wtforms.fields.choices import SelectChoice  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length, NumberRange  # type: ignore[import-untyped]

from app.authz import require_need, require_need_office, require_office
from app.extensions import db
from app.models import (
    Engagement,
    MemberRole,
    NeedStatus,
    Office,
    OfficeMember,
    Role,
    User,
)
from app.services.ledger import records_for_need
from app.services.needs import (
    EDITABLE,
    NeedConflict,
    NeedInput,
    publish_need,
    save_need,
    translations,
    withdraw_need,
)
from tsunagu_rules.types import NeedKind
from tsunagu_rules.value import estimate_value

bp = Blueprint("needs", __name__, url_prefix="/o/<int:office_id>/needs")

CATEGORIES = dict(
    zip(
        NeedKind,
        (
            "資料回収",
            "チェックと比較",
            "入力・取り込み",
            "月次の資料づくり",
            "道具の設定",
            "使い方を教える",
        ),
        strict=True,
    )
)
TASKS = {
    "reminder_flow": "催促の仕組み",
    "intake_form": "受け取りの入口",
    "compare_sheet": "過去比較の表",
    "import_rule": "取り込みの規則",
    "template": "ひな形",
    "api_link": "連携の設定",
    "manual": "手順書",
    "training": "勉強会",
}
STATUSES = {
    NeedStatus.DRAFT: "下書き",
    NeedStatus.BLOCKED: "公開できません",
    NeedStatus.PUBLISHED: "公開中",
    NeedStatus.WITHDRAWN: "取り下げ済み",
    NeedStatus.ENGAGED: "伴走中",
    NeedStatus.CLOSED: "終了",
}
WEEKDAYS = tuple(enumerate(("月曜", "火曜", "水曜", "木曜", "金曜", "土曜", "日曜")))


def weekday_options(mask: int) -> list[tuple[int, str]]:
    return [(day, label) for day, label in WEEKDAYS if mask & (1 << day)]


class NeedForm(FlaskForm):  # type: ignore[misc]
    body = TextAreaField(
        "困りごと",
        filters=[lambda value: value.strip() if value else value],
        validators=[
            Length(
                min=1, max=1000, message="困りごとは1〜1,000文字で入力してください。"
            ),
        ],
    )
    monthly_hours = IntegerField(
        "月にかかる時間（時間）",
        validators=[
            InputRequired("月にかかる時間を入力してください。"),
            NumberRange(min=1, max=400, message="1〜400の整数を入力してください。"),
        ],
    )
    weekdays = SelectMultipleField(
        "来てほしい曜日",
        choices=[SelectChoice(str(day), label) for day, label in WEEKDAYS],
        coerce=int,
        validators=[
            InputRequired("曜日を1つ以上選んでください。"),
        ],
    )
    manual_kind = SelectField(
        "型",
        default="",
        choices=[
            SelectChoice("", "型を選んでください"),
            *(SelectChoice(kind.value, label) for kind, label in CATEGORIES.items()),
        ],
    )

    def values(self) -> NeedInput:
        return NeedInput(
            self.body.data,
            self.monthly_hours.data,
            sum(1 << day for day in set(self.weekdays.data)),
            NeedKind(self.manual_kind.data) if self.manual_kind.data else None,
        )


def _location(office_id: int, need_id: int) -> str:
    return url_for("needs.detail", office_id=office_id, need_id=need_id)


def _detail(office: Office, need_id: int, error: str | None = None) -> str:
    need = require_need(office, need_id)
    rows = translations(need)
    user = cast(User, current_user)
    owner = (
        user.role == Role.OFFICE
        and db.session.scalar(
            select(OfficeMember.member_role).where(
                OfficeMember.office_id == office.id,
                OfficeMember.user_id == user.id,
            )
        )
        == MemberRole.OWNER
    )
    rate = (
        office.hourly_rate_yen
        if need.value_yen_month is None
        else need.value_yen_month // need.monthly_hours
    )
    return render_template(
        "needs/detail.html",
        need=need,
        rows=rows,
        ledger=[
            (row, json.loads(row.payload)["data"]) for row in records_for_need(need)
        ],
        engagement_id=db.session.scalar(
            select(Engagement.id).where(
                Engagement.office_id == office.id,
                Engagement.need_id == need.id,
            )
        ),
        office=office,
        owner=owner,
        editable=user.role == Role.OFFICE and need.status in EDITABLE,
        categories=CATEGORIES,
        tasks=TASKS,
        status=STATUSES[need.status],
        estimate=estimate_value(need.monthly_hours, rate),
        weekdays=[label for _, label in weekday_options(need.weekdays_mask)],
        error=error,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def new(office_id: int) -> ResponseReturnValue:
    office = require_office(office_id, write=True)
    return _save(office, NeedForm())


@bp.route("/<int:need_id>/edit", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def edit(office_id: int, need_id: int) -> ResponseReturnValue:
    office = require_need_office(office_id, need_id)
    need = require_need(office, need_id)
    if need.status not in EDITABLE:
        return _detail(office, need_id, "この困りごとは書き直せません。"), 409
    rows = translations(need)
    manual_kind = rows[0].category if rows and rows[0].source == "manual" else ""
    form = NeedForm(
        data={
            "body": need.body,
            "monthly_hours": need.monthly_hours,
            "weekdays": [day for day, _ in weekday_options(need.weekdays_mask)],
            "manual_kind": manual_kind,
        }
    )
    return _save(office, form, need_id)


def _save(
    office: Office, form: NeedForm, need_id: int | None = None
) -> ResponseReturnValue:
    error = None
    status = 200
    if form.validate_on_submit():
        try:
            need = save_need(
                office, cast(User, current_user).id, form.values(), need_id
            )
            return redirect(_location(office.id, need.id))
        except NeedConflict as exc:
            error, status = str(exc), 409
    elif form.is_submitted():
        status = 422
    return render_template(
        "needs/form.html", form=form, office=office, need_id=need_id, error=error
    ), status


@bp.get("/<int:need_id>")
@login_required  # type: ignore[untyped-decorator]
def detail(office_id: int, need_id: int) -> str:
    return _detail(require_office(office_id), need_id)


@bp.post("/<int:need_id>/publish")
@login_required  # type: ignore[untyped-decorator]
def publish(office_id: int, need_id: int) -> ResponseReturnValue:
    office = require_need_office(office_id, need_id, owner_only=True)
    try:
        publish_need(office, need_id, cast(User, current_user).id)
    except NeedConflict as exc:
        return _detail(office, need_id, str(exc)), 409
    return redirect(_location(office_id, need_id))


@bp.post("/<int:need_id>/withdraw")
@login_required  # type: ignore[untyped-decorator]
def withdraw(office_id: int, need_id: int) -> ResponseReturnValue:
    office = require_need_office(office_id, need_id, owner_only=True)
    try:
        withdraw_need(office, need_id, cast(User, current_user).id)
    except NeedConflict as exc:
        return _detail(office, need_id, str(exc)), 409
    return redirect(_location(office_id, need_id))
