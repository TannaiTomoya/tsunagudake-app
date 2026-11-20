from flask import Blueprint, Response, current_app, render_template
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from tsunagu_rules.ledger import REQUIRED_BOUNDARIES

bp = Blueprint("public", __name__)


@bp.get("/")
def index() -> str:
    return render_template("public/index.html")


@bp.get("/favicon.ico")
def favicon() -> Response:
    return current_app.send_static_file("icons/masu.ico")


@bp.get("/healthz")
def healthz() -> Response:
    try:
        db.session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        db.session.rollback()
        return Response("unavailable", status=503, mimetype="text/plain")
    return Response("ok", mimetype="text/plain")


@bp.get("/how")
def how() -> str:
    return render_template("public/how.html", boundaries=REQUIRED_BOUNDARIES)
