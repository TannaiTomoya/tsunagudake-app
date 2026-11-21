from typing import cast

from flask import Blueprint, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required

from app.authz import require_office_engagement
from app.models import User
from app.services import ai
from app.services.statement import month_ideas, valid_month

bp = Blueprint("drafts", __name__)


@bp.post("/o/<int:office_id>/engagements/<int:engagement_id>/ideas/summary")
@login_required  # type: ignore[untyped-decorator]
def idea_summary(office_id: int, engagement_id: int) -> ResponseReturnValue:
    """今月のメモの、まとめの下書きを出す。保存しない。決めるのは事務所。"""
    engagement = require_office_engagement(office_id, engagement_id, write=True)
    month = request.form.get("month", "")
    back = url_for(
        "matching.office_engagement",
        office_id=engagement.office_id,
        engagement_id=engagement.id,
        month=month if valid_month(month) else None,
        _anchor="ideas",
    )
    ideas = (
        [visit.idea for visit in month_ideas(engagement.id, month) if visit.idea]
        if valid_month(month)
        else []
    )
    # メモが無ければ、送る文が空なので、外は呼ばれない。
    summary = ai.summarize_ideas(engagement, cast(User, current_user).id, ideas)
    page = render_template(
        "drafts/summary.html", back=back, month=month, summary=summary, ideas=ideas
    )
    return page, 200 if summary else 409
