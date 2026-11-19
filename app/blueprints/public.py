from flask import Blueprint, Response, render_template
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db

bp = Blueprint("public", __name__)


@bp.get("/")
def index() -> str:
    return render_template("public/index.html")


@bp.get("/healthz")
def healthz() -> Response:
    try:
        db.session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        db.session.rollback()
        return Response("unavailable", status=503, mimetype="text/plain")
    return Response("ok", mimetype="text/plain")
