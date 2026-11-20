from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import (
    Engagement,
    EngagementStatus,
    Need,
    NeedStatus,
    Talent,
    User,
    utc_now,
)
from app.services.ledger import append_record
from app.services.mail import notify_office, notify_user

# 台帳に残すのは、選択肢の名前だけ（自由な文は残さない）。
END_REASONS = {
    "trial_only": "お試しで終える",
    "goal_met": "目的を果たした",
    "cannot_continue": "続けられなくなった",
    "other": "その他",
}


class EngagementConflict(Exception):
    pass


def end_engagement(
    engagement: Engagement, actor_id: int, reason: str, *, by_office: bool
) -> None:
    """伴走を終える。困りごとを「終了」にし、理由を台帳に残す。"""
    if reason not in END_REASONS:
        raise EngagementConflict("終える理由を、選択肢から選んでください。")
    # 合意・訪問・明細と同じ親（困りごと）を先にロックする。
    need = db.session.scalar(
        select(Need)
        .where(Need.id == engagement.need_id, Need.office_id == engagement.office_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = db.session.scalar(
        select(Engagement)
        .where(Engagement.id == engagement.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if need is None or row is None:
        db.session.rollback()
        raise EngagementConflict("伴走を確認できませんでした。")
    if row.status == EngagementStatus.ENDED:
        db.session.rollback()
        return
    row.status = EngagementStatus.ENDED
    row.ended_at = utc_now()
    row.end_reason = END_REASONS[reason]
    need.status = NeedStatus.CLOSED
    append_record(
        need,
        "ended",
        actor_id,
        {"reason": reason, "by": "office" if by_office else "talent"},
    )
    record_audit(
        "engagement_ended",
        actor_id=actor_id,
        office_id=row.office_id,
        target_type="engagement",
        target_id=row.id,
    )
    if by_office:
        person = db.session.scalar(
            select(User)
            .join(Talent, Talent.user_id == User.id)
            .where(Talent.id == row.talent_id)
        )
        if person is not None:
            notify_user(
                person, "伴走が終わりました", "伴走の画面で記録を確認できます。"
            )
    else:
        notify_office(
            row.office_id,
            "伴走が終わりました",
            "伴走の画面で記録を確認できます。",
            owners_only=True,
        )
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise EngagementConflict(
            "保存できませんでした。もう一度お試しください。"
        ) from None
