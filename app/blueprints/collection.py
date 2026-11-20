from typing import cast

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from sqlalchemy import select

from app.authz import require_office
from app.extensions import csrf, db
from app.models import MemberRole, Office, OfficeMember, Role, User, today_in_japan
from app.ratelimit import check_login, finish_login, login_buckets
from app.services.collection import (
    CollectionError,
    CollectionNotFound,
    board,
    carry_over,
    issue_token,
    office_for_token,
    parse_month,
    receive_counts,
    save_item,
    shift_month,
)

bp = Blueprint("collection", __name__)


def _page(
    office: Office,
    month: str,
    *,
    error: str | None = None,
    token: str | None = None,
    status: int = 200,
) -> ResponseReturnValue:
    user = cast(User, current_user)
    member_role = db.session.scalar(
        select(OfficeMember.member_role).where(
            OfficeMember.office_id == office.id, OfficeMember.user_id == user.id
        )
    )
    items = board(office.id, month)
    current = today_in_japan().strftime("%Y-%m")
    following = shift_month(month, 1)
    return (
        render_template(
            "collection/board.html",
            heading=office.name,
            office=office,
            month=month,
            month_label=f"{int(month[:4])}年{int(month[5:])}月",
            previous=shift_month(month, -1),
            following=following if following <= shift_month(current, 1) else None,
            items=items,
            ready=sum(item.is_ready for item in items),
            received=sum(item.received_count for item in items),
            can_carry=not items and bool(board(office.id, shift_month(month, -1))),
            can_write=user.role == Role.OFFICE,
            owner=member_role == MemberRole.OWNER,
            has_token=office.collection_token_hash is not None,
            error=error,
            token=token,
        ),
        status,
        # 合言葉を出す画面を、ブラウザや途中の機械に残さない。
        {"Cache-Control": "no-store"},
    )


@bp.route("/o/<int:office_id>/collection", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def office_board(office_id: int) -> ResponseReturnValue:
    user = cast(User, current_user)
    today = today_in_japan()
    if request.method == "GET":
        office = require_office(office_id)
        try:
            month = parse_month(request.args.get("month"), today)
        except CollectionError:
            abort(404)
        return _page(office, month)
    # 書けるのは事務所の代表と職員。事務所は URL の所属から決める。
    office = require_office(office_id, write=True)
    try:
        month = parse_month(request.form.get("month"), today)
    except CollectionError as exc:
        return _page(office, today.strftime("%Y-%m"), error=str(exc), status=422)
    action = request.form.get("action", "")
    try:
        if action == "carry":
            carry_over(office.id, user.id, month)
        else:
            save_item(office.id, user.id, month, request.form.get("client_no"), action)
    except CollectionNotFound:
        abort(404)
    except CollectionError as exc:
        return _page(office, month, error=str(exc), status=422)
    return redirect(
        url_for("collection.office_board", office_id=office.id, month=month)
    )


@bp.post("/o/<int:office_id>/collection/token")
@login_required  # type: ignore[untyped-decorator]
def token(office_id: int) -> ResponseReturnValue:
    # 合言葉を発行できるのは代表だけ。
    office = require_office(office_id, owner_only=True)
    try:
        issued = issue_token(office.id, cast(User, current_user).id)
    except CollectionNotFound:
        abort(404)
    except CollectionError as exc:
        return _page(
            office, today_in_japan().strftime("%Y-%m"), error=str(exc), status=409
        )
    # 合言葉は、この画面に1回だけ出す（URL・ログには入れない）。
    return _page(office, today_in_japan().strftime("%Y-%m"), token=issued)


@bp.post("/api/offices/<int:office_id>/collection-counts")
@csrf.exempt  # type: ignore[untyped-decorator]
def receive(office_id: int) -> ResponseReturnValue:
    """撮って送るだけ帳簿から、届いた件数だけを受け取る（機械の入口）。"""
    # 誤った合言葉は、接続元ごとに数える（事務所ごとに数えると、他人が止められる）。
    buckets = tuple(
        bucket
        for bucket in login_buckets("-", namespace="collection")
        if bucket.scope.endswith("_ip")
    )
    check_login(buckets)
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    office = (
        office_for_token(office_id, presented.strip())
        if scheme.lower() == "bearer" and presented.strip()
        else None
    )
    if office is None:
        finish_login(buckets, successful=False)
        db.session.commit()
        return jsonify(error="unauthorized"), 401, {"WWW-Authenticate": "Bearer"}
    try:
        saved = receive_counts(
            office.id, request.get_json(silent=True), today_in_japan()
        )
    except CollectionNotFound:
        return jsonify(error="unauthorized"), 401, {"WWW-Authenticate": "Bearer"}
    except CollectionError as exc:
        return jsonify(error=str(exc)), 422
    return jsonify(saved=saved)
