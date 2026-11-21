from typing import cast

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from sqlalchemy import select

from app.authz import require_office_engagement, require_talent_engagement
from app.extensions import db
from app.models import Engagement, EngagementStatus, Office, Talent, User
from app.services import ai
from app.services.messages import (
    BODY_MAX,
    TEMPLATES,
    MessageConflict,
    MessageError,
    clean_body,
    japan_time,
    mark_read,
    partners,
    send_message,
    thread,
)

bp = Blueprint("messages", __name__)


def _actor() -> User:
    return cast(User, current_user)


def _urls(engagement: Engagement, *, office_side: bool) -> tuple[str, str]:
    if office_side:
        return (
            url_for(
                "messages.office_thread",
                office_id=engagement.office_id,
                engagement_id=engagement.id,
            ),
            url_for(
                "matching.office_engagement",
                office_id=engagement.office_id,
                engagement_id=engagement.id,
            ),
        )
    return (
        url_for("messages.talent_thread", engagement_id=engagement.id),
        url_for("matching.talent_engagement", engagement_id=engagement.id),
    )


def _partner_name(engagement: Engagement, *, office_side: bool) -> str:
    if office_side:
        name = db.session.scalar(
            select(User.display_name)
            .join(Talent, Talent.user_id == User.id)
            .where(Talent.id == engagement.talent_id)
        )
    else:
        name = db.session.scalar(
            select(Office.name).where(Office.id == engagement.office_id)
        )
    return name or "相手"


def _page(
    engagement: Engagement,
    *,
    office_side: bool,
    draft: str = "",
    template: str = "",
    error: str | None = None,
    confirming: bool = False,
    sent: bool = False,
    arrived: int = 0,
    auto_draft: bool = False,
) -> str:
    here, back = _urls(engagement, office_side=office_side)
    return render_template(
        "messages/thread.html",
        engagement=engagement,
        partner=_partner_name(engagement, office_side=office_side),
        office_side=office_side,
        here=here,
        back=back,
        rows=[
            (message, name, japan_time(message.created_at))
            for message, name in thread(engagement.id)
        ],
        templates=TEMPLATES,
        template=template if template in TEMPLATES else "",
        draft=draft,
        body_max=BODY_MAX,
        error=error,
        confirming=confirming,
        sent=sent,
        arrived=arrived,
        ended=engagement.status == EngagementStatus.ENDED,
        auto_draft=auto_draft,
        can_draft=ai.available(engagement.office_id),
    )


def _handle(engagement: Engagement, *, office_side: bool) -> ResponseReturnValue:
    if request.method == "GET":
        arrived = mark_read(engagement, office_side=office_side)
        key = request.args.get("template", "")
        return _page(
            engagement,
            office_side=office_side,
            draft=TEMPLATES[key][1] if key in TEMPLATES else "",
            template=key,
            sent=request.args.get("sent") == "1",
            arrived=arrived,
        )
    action = request.form.get("action", "")
    raw = request.form.get("body", "")
    template = request.form.get("template", "")
    # 長すぎる文も、直せるように返す（返すのは、上限の少し先まで）。
    shown = raw[: BODY_MAX * 3]
    if action == "edit":
        return _page(
            engagement, office_side=office_side, draft=shown, template=template
        )
    if action not in {"confirm", "send", "draft"}:
        abort(400)
    if engagement.status == EngagementStatus.ENDED:
        message = "終わった伴走には、メッセージを送れません。これまでの分は読めます。"
        return render_template("error.html", message=message), 409
    if action == "draft":
        # 入力欄のメモから下書きを作るだけ。送らない。送るのは人。
        written = ai.draft_message(
            engagement,
            _actor().id,
            raw[:BODY_MAX],
            TEMPLATES[template][0] if template in TEMPLATES else "",
        )
        if written is None:
            page = _page(
                engagement,
                office_side=office_side,
                draft=shown,
                template=template,
                error="今は、下書きを作れません。ひな形から始めるか、そのまま書いてください。",
            )
            return page, 409
        return _page(
            engagement,
            office_side=office_side,
            draft=written,
            template=template,
            auto_draft=True,
        )
    try:
        if action == "confirm":
            return _page(
                engagement,
                office_side=office_side,
                draft=clean_body(raw),
                template=template,
                confirming=True,
            )
        send_message(engagement, _actor().id, raw, template, from_office=office_side)
    except MessageError as exc:
        page = _page(
            engagement,
            office_side=office_side,
            draft=shown,
            template=template,
            error=str(exc),
        )
        return page, 422
    except MessageConflict as exc:
        return render_template("error.html", message=str(exc)), 409
    here, _ = _urls(engagement, office_side=office_side)
    return redirect(here + "?sent=1")


@bp.route(
    "/o/<int:office_id>/engagements/<int:engagement_id>/messages",
    methods=["GET", "POST"],
)
@login_required  # type: ignore[untyped-decorator]
def office_thread(office_id: int, engagement_id: int) -> ResponseReturnValue:
    # 読めるのも送れるのも、事務所の代表・職員だけ（運営は読めない）。
    engagement = require_office_engagement(office_id, engagement_id, write=True)
    return _handle(engagement, office_side=True)


@bp.route("/t/engagements/<int:engagement_id>/messages", methods=["GET", "POST"])
@login_required  # type: ignore[untyped-decorator]
def talent_thread(engagement_id: int) -> ResponseReturnValue:
    engagement = require_talent_engagement(engagement_id, write=True)
    return _handle(engagement, office_side=False)


@bp.get("/account/messages")
@login_required  # type: ignore[untyped-decorator]
def inbox() -> str:
    # 自分の伴走の相手だけ。運営・自治体には、相手がいない。
    return render_template(
        "messages/inbox.html", partners=partners(_actor(), include_ended=True)
    )
