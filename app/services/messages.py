from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import url_for
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.extensions import db
from app.models import (
    Engagement,
    EngagementStatus,
    Message,
    Need,
    Office,
    OfficeMember,
    Role,
    Talent,
    User,
    utc_now,
)
from app.services.mail import notify_office, notify_user

BODY_MAX = 400
# 1人が、1つの伴走に、24時間で送れる数。
DAILY_MAX = 30
PARTNER_MAX = 3
REMOVED_BODY = "（退会により、本文を消しました）"
# ひな形。選ぶと入力欄に入り、送る前に直せる。
TEMPLATES = {
    "schedule": (
        "訪問の日程",
        "次の訪問の日程を相談させてください。ご都合のよい日を教えてください。",
    ),
    "materials": (
        "資料のお願い",
        "次の訪問までに、資料のご用意をお願いします。"
        "そろったら、このメッセージでお知らせください。",
    ),
    "thanks": (
        "お礼",
        "本日はありがとうございました。次回もよろしくお願いします。",
    ),
}


class MessageError(Exception):
    pass


class MessageConflict(Exception):
    pass


@dataclass(frozen=True)
class Partner:
    name: str
    link: str
    unread: int


def clean_body(raw: str | None) -> str:
    text = (raw or "").replace("\r\n", "\n").strip()
    if not 1 <= len(text) <= BODY_MAX:
        raise MessageError(f"本文は1〜{BODY_MAX}文字で入力してください。")
    return text


def thread(engagement_id: int) -> list[tuple[Message, str]]:
    """新しい順。送った人の表示名を付ける。"""
    rows = db.session.execute(
        select(Message, User.display_name)
        .join(User, User.id == Message.sender_user_id)
        .where(Message.engagement_id == engagement_id)
        .order_by(Message.id.desc())
    )
    return [(message, name) for message, name in rows]


def unread_count(engagement_id: int, *, office_side: bool) -> int:
    return (
        db.session.scalar(
            select(func.count(Message.id)).where(
                Message.engagement_id == engagement_id,
                Message.from_office.is_(not office_side),
                Message.read_at.is_(None),
            )
        )
        or 0
    )


def mark_read(engagement: Engagement, *, office_side: bool) -> int:
    """相手からの分に、既読の印を付ける。付けた数を返す。"""
    rows = db.session.scalars(
        select(Message)
        .where(
            Message.engagement_id == engagement.id,
            Message.from_office.is_(not office_side),
            Message.read_at.is_(None),
        )
        .with_for_update()
    ).all()
    now = utc_now()
    # 業務の表は、1行ずつ変える。
    for row in rows:
        row.read_at = now
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        return 0
    return len(rows)


def send_message(
    engagement: Engagement,
    actor_id: int,
    raw_body: str | None,
    template: str | None,
    *,
    from_office: bool,
) -> None:
    body = clean_body(raw_body)
    # 合意・訪問・明細と同じ親（困りごと）を先にロックする。
    need = db.session.scalar(
        select(Need)
        .where(Need.id == engagement.need_id, Need.office_id == engagement.office_id)
        .with_for_update()
    )
    status = db.session.scalar(
        select(Engagement.status).where(Engagement.id == engagement.id)
    )
    if need is None or status is None or status == EngagementStatus.ENDED:
        db.session.rollback()
        raise MessageConflict(
            "終わった伴走には、メッセージを送れません。これまでの分は読めます。"
        )
    recent = db.session.scalar(
        select(func.count(Message.id)).where(
            Message.engagement_id == engagement.id,
            Message.sender_user_id == actor_id,
            Message.created_at >= utc_now() - timedelta(hours=24),
        )
    )
    if (recent or 0) >= DAILY_MAX:
        db.session.rollback()
        raise MessageConflict(
            f"この伴走に送れるのは、24時間で{DAILY_MAX}通までです。"
            "時間をおいてお試しください。"
        )
    db.session.add(
        Message(
            engagement_id=engagement.id,
            sender_user_id=actor_id,
            from_office=from_office,
            template=template if template in TEMPLATES else None,
            body=body,
        )
    )
    # メールには本文を入れない。
    subject = "メッセージが届いています"
    note = "伴走の画面の「メッセージ」で読めます。"
    if from_office:
        person = db.session.scalar(
            select(User)
            .join(Talent, Talent.user_id == User.id)
            .where(Talent.id == engagement.talent_id)
        )
        if person is not None:
            notify_user(person, subject, note)
    else:
        notify_office(engagement.office_id, subject, note)
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise MessageConflict("送れませんでした。もう一度お試しください。") from None


def _unread_by_engagement(*, office_side: bool) -> dict[int, int]:
    rows = db.session.execute(
        select(Message.engagement_id, func.count(Message.id))
        .where(
            Message.from_office.is_(not office_side),
            Message.read_at.is_(None),
        )
        .group_by(Message.engagement_id)
    )
    return {engagement_id: count for engagement_id, count in rows}


def partners(actor: User, *, include_ended: bool = False) -> list[Partner]:
    """今の伴走の相手を、未読の多い順・新しい順に3人まで。"""
    found: list[tuple[int, int, Partner]] = []
    if actor.role == Role.OFFICE:
        query = (
            select(Engagement, User.display_name)
            .join(Talent, Talent.id == Engagement.talent_id)
            .join(User, User.id == Talent.user_id)
            .join(Office, Office.id == Engagement.office_id)
            .join(OfficeMember, OfficeMember.office_id == Office.id)
            .where(OfficeMember.user_id == actor.id, Office.is_active.is_(True))
        )
        office_side = True
    elif actor.role == Role.TSUNAGITE:
        query = (
            select(Engagement, Office.name)
            .join(Talent, Talent.id == Engagement.talent_id)
            .join(Office, Office.id == Engagement.office_id)
            .where(Talent.user_id == actor.id)
        )
        office_side = False
    else:
        return []
    unread = _unread_by_engagement(office_side=office_side)
    for engagement, name in db.session.execute(query):
        waiting = unread.get(engagement.id, 0)
        if engagement.status == EngagementStatus.ENDED and not (
            include_ended and waiting
        ):
            continue
        link = (
            url_for(
                "messages.office_thread",
                office_id=engagement.office_id,
                engagement_id=engagement.id,
            )
            if office_side
            else url_for("messages.talent_thread", engagement_id=engagement.id)
        )
        found.append((waiting, engagement.id, Partner(name, link, waiting)))
    found.sort(key=lambda item: (-item[0], -item[1]))
    return [partner for _, _, partner in found[:PARTNER_MAX]]


def japan_time(moment: datetime) -> str:
    local = moment + timedelta(hours=9)
    weekday = "月火水木金土日"[local.weekday()]
    return f"{local.month}月{local.day}日({weekday}) {local.hour}:{local.minute:02d}"


def erase_sent_by(user_id: int) -> None:
    """退会した人が送った本文を消す（行は、相手の画面の順番のために残す）。"""
    for row in db.session.scalars(
        select(Message).where(Message.sender_user_id == user_id)
    ):
        row.body = REMOVED_BODY


def erase_for_office(office_id: int) -> None:
    for row in db.session.scalars(
        select(Message)
        .join(Engagement, Engagement.id == Message.engagement_id)
        .where(Engagement.office_id == office_id)
    ):
        row.body = REMOVED_BODY
