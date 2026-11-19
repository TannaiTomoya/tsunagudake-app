from flask import Blueprint, render_template, request
from flask.typing import ResponseReturnValue
from flask_wtf import FlaskForm
from wtforms import PasswordField  # type: ignore[import-untyped]
from wtforms.validators import InputRequired, Length  # type: ignore[import-untyped]

from app.blueprints.auth import start_session
from app.models import Role
from app.services.auth import authenticate
from app.services.demo import ACCOUNTS

bp = Blueprint("demo", __name__, url_prefix="/demo")

LABELS = {
    Role.OFFICE: "会計事務所として見る",
    Role.TSUNAGITE: "つなぎ手として見る",
    Role.MUNICIPALITY: "自治体として見る",
}


class DemoEntryForm(FlaskForm):  # type: ignore[misc]
    password = PasswordField(
        "発表用の合言葉", validators=[InputRequired(), Length(max=1024)]
    )


@bp.route("/enter", methods=["GET", "POST"])
def enter() -> ResponseReturnValue:
    form = DemoEntryForm()
    if form.validate_on_submit():
        email = next(
            (
                address
                for role, address in ACCOUNTS.items()
                if role == request.form.get("role")
            ),
            None,
        )
        if email is not None:
            user = authenticate(email, form.password.data, demo_only=True)
            if user is not None and user.role.value == request.form.get("role"):
                return start_session(user)
    if form.is_submitted():
        return render_template(
            "auth/demo_enter.html", form=form, labels=LABELS, failed=True
        ), 401
    return render_template("auth/demo_enter.html", form=form, labels=LABELS)
