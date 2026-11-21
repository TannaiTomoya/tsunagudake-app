from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import CheckSummary, Engagement, EngagementStatus, Need

COUNT_MAX = 9999
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
COUNT_ERROR = (
    "過去比較の件数は、0〜9999の整数で入れてください。"
    "印の数は、比べた科目の数までです。比べた科目と比較できなかった科目の合計は1以上にしてください。"
)


class CheckError(ValueError):
    pass


class CheckConflict(Exception):
    pass


@dataclass(frozen=True)
class CheckTotal:
    month: str
    runs: int
    compared: int
    flagged: int
    uncomparable: int


def _count(raw: str | None) -> int:
    text = (raw or "").strip().translate(FULLWIDTH_DIGITS)
    if not (text.isascii() and text.isdigit() and len(text) <= 4):
        raise CheckError(COUNT_ERROR)
    return int(text)


def save_summary(
    engagement: Engagement,
    actor_id: int,
    compared: str | None,
    flagged: str | None,
    uncomparable: str | None,
    today: date,
) -> None:
    """過去比較の件数を1回分足す。受け取るのは3つの整数だけ。"""
    counts = (_count(compared), _count(flagged), _count(uncomparable))
    if counts[1] > counts[0] or counts[0] + counts[2] < 1:
        raise CheckError(COUNT_ERROR)
    # 訪問・明細と同じ親（困りごと）を先にロックしてから、伴走の状態を確かめる。
    db.session.scalar(
        select(Need.id)
        .where(Need.id == engagement.need_id, Need.office_id == engagement.office_id)
        .with_for_update()
    )
    status = db.session.scalar(
        select(Engagement.status).where(Engagement.id == engagement.id)
    )
    if status is None or status == EngagementStatus.ENDED:
        db.session.rollback()
        raise CheckConflict("終わった伴走には、件数を記録できません。")
    db.session.add(
        CheckSummary(
            engagement_id=engagement.id,
            month=today.strftime("%Y-%m"),
            compared=counts[0],
            flagged=counts[1],
            uncomparable=counts[2],
            recorded_by=actor_id,
        )
    )
    record_audit(
        "check_summary_saved",
        actor_id=actor_id,
        office_id=engagement.office_id,
        target_type="engagement",
        target_id=engagement.id,
    )
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise CheckConflict("保存できませんでした。もう一度お試しください。") from None


def totals_for(engagement_id: int) -> list[CheckTotal]:
    """月ごとの合計（新しい月から）。"""
    rows = db.session.execute(
        select(
            CheckSummary.month,
            func.count(CheckSummary.id),
            func.sum(CheckSummary.compared),
            func.sum(CheckSummary.flagged),
            func.sum(CheckSummary.uncomparable),
        )
        .where(CheckSummary.engagement_id == engagement_id)
        .group_by(CheckSummary.month)
        .order_by(CheckSummary.month.desc())
    )
    return [CheckTotal(month, *(int(value) for value in rest)) for month, *rest in rows]
