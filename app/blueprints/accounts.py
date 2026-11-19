from flask import Blueprint, redirect, render_template, url_for
from flask.typing import ResponseReturnValue
from flask_wtf import FlaskForm
from werkzeug.exceptions import TooManyRequests
from wtforms import EmailField, HiddenField, PasswordField, StringField  # type: ignore[import-untyped]
from wtforms.validators import EqualTo, InputRequired, Length, Regexp  # type: ignore[import-untyped]

from app.services.accounts import (
    AccountError,
    accept_invitation,
    request_reset,
    reset_password,
)

bp = Blueprint("accounts", __name__)


class ResetRequestForm(FlaskForm):  # type: ignore[misc]
    email = EmailField("メールアドレス", validators=[InputRequired(), Length(max=255)])


class PasswordForm(FlaskForm):  # type: ignore[misc]
    token = HiddenField(validators=[InputRequired(), Regexp(r"[A-Za-z0-9_-]{43}\Z")])
    password = PasswordField(
        "新しいパスワード", validators=[InputRequired(), Length(12, 1024)]
    )
    confirmation = PasswordField(
        "新しいパスワード（確認）", validators=[EqualTo("password")]
    )


class InvitationForm(PasswordForm):
    display_name = StringField(
        "表示名",
        filters=[lambda value: value.strip() if value else value],
        validators=[InputRequired(), Length(1, 100)],
    )


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot() -> ResponseReturnValue:
    form = ResetRequestForm()
    if form.is_submitted():
        if form.validate():
            try:
                request_reset(form.email.data)
            except TooManyRequests:
                pass
        return render_template("auth/forgot.html", form=form, completed=True)
    return render_template("auth/forgot.html", form=form)


@bp.route("/accept-invite", methods=["GET", "POST"])
def accept() -> ResponseReturnValue:
    form = InvitationForm()
    error = None
    if form.validate_on_submit():
        try:
            accept_invitation(
                form.token.data, form.display_name.data, form.password.data
            )
            return redirect(url_for("auth.login"))
        except AccountError as exc:
            error = str(exc)
    return _password_page(form, "招待を受ける", error)


@bp.route("/reset-password", methods=["GET", "POST"])
def reset() -> ResponseReturnValue:
    form = PasswordForm()
    error = None
    if form.validate_on_submit():
        try:
            reset_password(form.token.data, form.password.data)
            return redirect(url_for("auth.login"))
        except AccountError as exc:
            error = str(exc)
    return _password_page(form, "パスワードを再設定する", error)


def _password_page(
    form: PasswordForm, heading: str, error: str | None
) -> ResponseReturnValue:
    if form.is_submitted() and error is None:
        error = (
            "有効なリンクをメールから開いてください。"
            if form.token.errors
            else "表示名と、12文字以上のパスワード・確認欄をご確認ください。"
            if isinstance(form, InvitationForm)
            else "12文字以上のパスワードと確認欄をご確認ください。"
        )
    return (
        render_template(
            "auth/set_password.html", form=form, heading=heading, error=error
        ),
        422 if error else 200,
        {"Cache-Control": "no-store"},
    )
