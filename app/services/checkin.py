import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import (
    Checkin,
    Engagement,
    EngagementStatus,
    Need,
    Office,
    OfficeMember,
    User,
)
from app.services.mail import enqueue
from tsunagu_rules.distance import (
    E6,
    LAT_MAX_E6,
    LON_MAX_E6,
    NEAR_RADIUS_M,
    is_near,
)

# 着くまでの時間は、選択肢だけ（分）。
ETA_CHOICES = (15, 30, 45, 60, 90, 120)
# 押し直しのメールは、前に押してから10分たってから（仮の数字）。画面の予定は毎回変わる。
HEADING_MAIL_GAP = timedelta(minutes=10)
ADDRESS_MAX = 200
LOCATION_ERROR = (
    "緯度は -90〜90、経度は -180〜180 の数で、両方を入力してください。"
    "消すときは、両方を空にしてください。"
)
ARRIVE_ERROR = (
    "「位置を1回だけ送る」に印を付け、端末の位置を使えるようにしてから押してください。"
)
FAR_ERROR = (
    f"事務所の近く（{NEAR_RADIUS_M}m以内）で押してください。"
    "送られた位置は保存していません。"
)


class CheckinError(ValueError):
    pass


class CheckinConflict(Exception):
    pass


class CheckinOfficeNotFound(LookupError):
    pass


def _degrees_e6(raw: str | None, limit: int) -> int | None:
    text = unicodedata.normalize("NFKC", raw or "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise CheckinError(LOCATION_ERROR) from None
    if not value.is_finite() or len(text) > 20:
        raise CheckinError(LOCATION_ERROR)
    scaled = int(value * E6)
    if not -limit <= scaled <= limit:
        raise CheckinError(LOCATION_ERROR)
    return scaled


def japan_clock(moment: datetime) -> str:
    local = moment + timedelta(hours=9)
    return f"{local.hour}:{local.minute:02d}"


def save_location(
    office_id: int,
    actor_id: int,
    raw_address: str | None,
    raw_lat: str | None,
    raw_lon: str | None,
) -> None:
    """事務所の住所と位置を入れる。空は「登録していない」に戻す。"""
    address = (raw_address or "").strip()
    if len(address) > ADDRESS_MAX:
        raise CheckinError(f"住所は{ADDRESS_MAX}文字以内で入力してください。")
    lat = _degrees_e6(raw_lat, LAT_MAX_E6)
    lon = _degrees_e6(raw_lon, LON_MAX_E6)
    if (lat is None) != (lon is None):
        raise CheckinError(LOCATION_ERROR)
    office = db.session.scalar(
        select(Office)
        .where(Office.id == office_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if office is None or not office.is_active:
        db.session.rollback()
        raise CheckinOfficeNotFound
    if office.is_demo:
        db.session.rollback()
        raise CheckinConflict(
            "デモの事務所には、住所と位置を登録できません。"
            "本物の情報を入れないでください。"
        )
    office.address = address or None
    office.lat_e6 = lat
    office.lon_e6 = lon
    record_audit(
        "office_location_saved",
        actor_id=actor_id,
        office_id=office.id,
        target_type="office",
        target_id=office.id,
    )
    db.session.commit()


def _open_row(
    engagement: Engagement, actor_id: int, day: date
) -> tuple[Checkin, Office]:
    # 合意・訪問・明細と同じ親（困りごと）を先にロックする。
    need = db.session.scalar(
        select(Need)
        .where(Need.id == engagement.need_id, Need.office_id == engagement.office_id)
        .with_for_update()
    )
    status = db.session.scalar(
        select(Engagement.status).where(Engagement.id == engagement.id)
    )
    office = db.session.get(Office, engagement.office_id)
    if need is None or office is None or status is None:
        db.session.rollback()
        raise CheckinConflict("伴走を確認できませんでした。")
    if status == EngagementStatus.ENDED:
        db.session.rollback()
        raise CheckinConflict("終わった伴走には、到着を記録できません。")
    row = db.session.scalar(
        select(Checkin)
        .where(Checkin.engagement_id == engagement.id, Checkin.day == day)
        .with_for_update()
    )
    if row is None:
        row = Checkin(engagement_id=engagement.id, day=day, created_by=actor_id)
        db.session.add(row)
    return row, office


def _commit() -> None:
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise CheckinConflict(
            "保存できませんでした。もう一度お試しください。"
        ) from None


def head_out(
    engagement: Engagement,
    actor_id: int,
    raw_minutes: str | None,
    day: date,
    now: datetime,
) -> None:
    """「今から向かいます」。着く予定だけを知らせる。位置は受け取らない。"""
    text = (raw_minutes or "").strip()
    if not (text.isascii() and text.isdigit()) or int(text) not in ETA_CHOICES:
        raise CheckinError("着くまでの時間を、選択肢から選んでください。")
    row, office = _open_row(engagement, actor_id, day)
    if row.arrived_at is not None:
        db.session.rollback()
        raise CheckinConflict("今日は、もう「着きました」を押しています。")
    notify = row.heading_at is None or now - row.heading_at >= HEADING_MAIL_GAP
    row.heading_at = now
    row.eta_at = now + timedelta(minutes=int(text))
    body = (
        f"つなぎ手が向かっています。着く予定は {japan_clock(row.eta_at)} ごろです。"
        "伴走の画面でも確かめられます。"
    )
    # すぐに知らせる（翌朝のまとめでは遅い）。
    members = (
        db.session.scalars(
            select(User)
            .join(OfficeMember, OfficeMember.user_id == User.id)
            .where(OfficeMember.office_id == office.id, User.is_active.is_(True))
            .order_by(User.id)
        )
        if notify
        else ()
    )
    for member in members:
        enqueue(
            member.email,
            "つなぐだけ：つなぎ手が向かっています",
            body,
            demo=member.is_demo,
            expires_at=row.eta_at + timedelta(hours=2),
        )
    _commit()


def arrive(
    engagement: Engagement,
    actor_id: int,
    raw_lat: str | None,
    raw_lon: str | None,
    day: date,
    now: datetime,
    *,
    consent: bool,
) -> None:
    """「着きました」。残すのは、近くで押した事実と時刻だけ。位置は残さない。"""
    row, office = _open_row(engagement, actor_id, day)
    if row.arrived_at is not None:
        db.session.rollback()
        return
    near: bool | None = None
    # デモと、位置を登録していない事務所では、位置を受け取らない。
    if uses_location(office):
        assert office.lat_e6 is not None and office.lon_e6 is not None
        try:
            lat = _degrees_e6(raw_lat, LAT_MAX_E6)
            lon = _degrees_e6(raw_lon, LON_MAX_E6)
        except CheckinError:
            lat = lon = None
        if not consent or lat is None or lon is None:
            db.session.rollback()
            raise CheckinError(ARRIVE_ERROR)
        if not is_near(lat, lon, office.lat_e6, office.lon_e6):
            db.session.rollback()
            raise CheckinError(FAR_ERROR)
        near = True
    row.arrived_at = now
    row.near = near
    record_audit(
        "checkin_arrived",
        actor_id=actor_id,
        office_id=office.id,
        target_type="engagement",
        target_id=engagement.id,
    )
    _commit()


def checkin_on(engagement_id: int, day: date) -> Checkin | None:
    return db.session.scalar(
        select(Checkin).where(
            Checkin.engagement_id == engagement_id, Checkin.day == day
        )
    )


def arrivals(engagement_id: int) -> dict[date, bool | None]:
    """着いた日 → 近くで押したか（None は確かめていない）。"""
    rows = db.session.execute(
        select(Checkin.day, Checkin.near).where(
            Checkin.engagement_id == engagement_id, Checkin.arrived_at.is_not(None)
        )
    )
    return {day: near for day, near in rows}


def uses_location(office: Office) -> bool:
    return (
        not office.is_demo and office.lat_e6 is not None and office.lon_e6 is not None
    )
