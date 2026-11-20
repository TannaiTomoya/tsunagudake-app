from typing import cast

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import (  # type: ignore[import-untyped]
    BooleanField,
    HiddenField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
)
from wtforms.fields.choices import SelectChoice  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length, NumberRange, Optional, Regexp  # type: ignore[import-untyped]

from app.blueprints.needs import CATEGORIES, WEEKDAYS
from app.extensions import db
from app.models import (
    BookkeepingLevel,
    Role,
    Talent,
    TalentAffiliation,
    TalentSkill,
    TalentSkillKind,
    TrustKind,
    User,
    today_in_japan,
)
from app.services.municipalities import chosen_prefecture, prefectures, towns_in
from app.services.trust import (
    PLEDGE_TERMS,
    PLEDGE_VERSION,
    ProfileInput,
    TrustConflict,
    add_trust_record,
    save_profile,
)
from tsunagu_rules.types import NeedKind

bp = Blueprint("talent", __name__, url_prefix="/t")

TOOLS = {
    "freee": "freee",
    "mf": "マネーフォワード",
    "yayoi": "弥生",
    "excel": "Excel",
    "sheets": "Google スプレッドシート",
    "python": "Python",
    "other": "その他",
}
AFFILIATIONS = {
    TalentAffiliation.NONE: "なし",
    TalentAffiliation.GRADUATE: "修了生",
    TalentAffiliation.KYORYOKUTAI: "地域おこし協力隊",
    TalentAffiliation.OTHER: "その他",
}


class ProfileForm(FlaskForm):  # type: ignore[misc]
    municipality = SelectField("住む市町村", coerce=int)
    grade = SelectField(
        "簿記の級",
        choices=[
            SelectChoice(
                grade.value, "なし" if grade == BookkeepingLevel.NONE else f"{grade}級"
            )
            for grade in BookkeepingLevel
        ],
    )
    weekdays = SelectMultipleField(
        "通える曜日",
        coerce=int,
        choices=[SelectChoice(str(day), label) for day, label in WEEKDAYS],
    )
    tools = SelectMultipleField(
        "使える道具",
        choices=[SelectChoice(value, label) for value, label in TOOLS.items()],
    )
    strengths = SelectMultipleField(
        "得意な業務",
        choices=[
            SelectChoice(value.value, label) for value, label in CATEGORIES.items()
        ],
    )
    affiliation = SelectField(
        "所属",
        choices=[SelectChoice(key.value, label) for key, label in AFFILIATIONS.items()],
    )
    capacity = IntegerField(
        "同時に受け持てる件数", validators=[InputRequired(), NumberRange(1, 5)]
    )
    payout = StringField(
        "振込先（銀行・支店・種別・口座番号・名義）", validators=[Length(max=200)]
    )
    registration = StringField(
        "インボイス登録番号（任意）", validators=[Optional(), Regexp(r"^T[0-9]{13}$")]
    )
    clear_payout = BooleanField("登録済みの振込先を消す")
    listed = BooleanField("候補に出る")


class PledgeForm(FlaskForm):  # type: ignore[misc]
    version = HiddenField(default=PLEDGE_VERSION, validators=[InputRequired()])
    agree = BooleanField(
        "内容を読み、守秘の誓約に同意します", validators=[InputRequired()]
    )


def _actor() -> User:
    user = cast(User, current_user)
    if user.role != Role.TSUNAGITE:
        abort(404)
    return user


def _profile(user_id: int) -> Talent | None:
    return db.session.scalar(select(Talent).where(Talent.user_id == user_id))


@bp.route("/profile", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def profile() -> ResponseReturnValue:
    user = _actor()
    talent = _profile(user.id)
    skills = (
        list(
            db.session.scalars(
                select(TalentSkill).where(TalentSkill.talent_id == talent.id)
            )
        )
        if talent
        else []
    )
    form = ProfileForm(
        data={
            "municipality": talent.municipality_id if talent else None,
            "grade": talent.bookkeeping_level.value
            if talent
            else BookkeepingLevel.NONE.value,
            "weekdays": [
                day
                for day, _ in WEEKDAYS
                if talent and talent.weekdays_mask & (1 << day)
            ],
            "affiliation": talent.affiliation.value
            if talent
            else TalentAffiliation.NONE.value,
            "capacity": talent.capacity if talent else 2,
            "registration": talent.invoice_registration if talent else None,
            "listed": bool(talent and talent.is_listed),
            "tools": [
                skill.tag for skill in skills if skill.kind == TalentSkillKind.TOOL
            ],
            "strengths": [
                skill.tag for skill in skills if skill.kind == TalentSkillKind.STRENGTH
            ],
        }
    )
    # 都道府県 → 市町村の順に選ぶ。デモの人は架空の町だけ、通常の人は実在の町だけ。
    groups = prefectures(demo=user.is_demo)
    prefecture = chosen_prefecture(
        groups,
        request.args.get("prefecture"),
        talent.municipality_id if talent else None,
    )
    form.municipality.choices = [
        SelectChoice("0", "選択してください"),
        *(
            SelectChoice(str(town.id), town.name)
            for town in towns_in(prefecture, demo=user.is_demo)
        ),
    ]
    if request.method == "GET" and form.municipality.data not in {
        int(choice.value) for choice in form.municipality.choices
    }:
        form.municipality.data = 0
    error = None
    status = 200
    if form.validate_on_submit() and form.municipality.data:
        try:
            save_profile(
                user.id,
                ProfileInput(
                    municipality_id=form.municipality.data,
                    grade=BookkeepingLevel(form.grade.data),
                    weekdays_mask=sum(1 << day for day in set(form.weekdays.data)),
                    affiliation=TalentAffiliation(form.affiliation.data),
                    capacity=form.capacity.data,
                    is_listed=form.listed.data,
                    payout=""
                    if form.clear_payout.data
                    else (form.payout.data or "").strip() or None,
                    registration=(form.registration.data or "").strip()
                    if "registration" in request.form
                    else None,
                    tools=frozenset(form.tools.data),
                    strengths=frozenset(NeedKind(kind) for kind in form.strengths.data),
                ),
            )
            return redirect(url_for("auth.talent_home"))
        except TrustConflict as exc:
            error, status = str(exc), 409
    elif form.is_submitted():
        error, status = "市町村と選択項目、受け持ち件数（1〜5）をご確認ください。", 422
    return render_template(
        "auth/talent_profile.html",
        form=form,
        error=error,
        prefectures=groups,
        prefecture=prefecture,
    ), status


@bp.route("/pledge", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def pledge() -> ResponseReturnValue:
    user = _actor()
    talent = _profile(user.id)
    if talent is None:
        return redirect(url_for("talent.profile"))
    form = PledgeForm()
    error = None
    status = 200
    if form.validate_on_submit() and form.version.data == PLEDGE_VERSION:
        try:
            add_trust_record(talent.id, TrustKind.PLEDGE, user.id, today_in_japan())
            return redirect(url_for("auth.talent_home"))
        except TrustConflict as exc:
            error, status = str(exc), 409
    elif form.is_submitted():
        error, status = "内容を読み、同意する場合は確認欄を選んでください。", 422
    return render_template(
        "auth/talent_pledge.html", form=form, terms=PLEDGE_TERMS, error=error
    ), status
