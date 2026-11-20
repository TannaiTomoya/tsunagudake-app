from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import Municipality, Office, OfficeRoute, TravelTime, User, utc_now
from app.services.municipalities import allowed

MINUTES_MAX = 300
ID_MAX = 2**31 - 1
MINUTES_ERROR = "車での時間は、1〜300分の整数で入力してください。"
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


class RouteError(ValueError):
    pass


class RouteConflict(Exception):
    pass


class RouteOfficeNotFound(LookupError):
    pass


@dataclass(frozen=True)
class RouteRow:
    prefecture: str
    town: str
    minutes: int
    confirmed_at: datetime | None
    confirmed_by: str | None


def routes_for(office: Office) -> list[RouteRow]:
    """事務所までの移動時間。デモは架空の表、通常は確かめた経路。"""
    if office.is_demo:
        rows = db.session.execute(
            select(Municipality.region_group, Municipality.name, TravelTime.minutes)
            .join(TravelTime, TravelTime.from_municipality_id == Municipality.id)
            .where(TravelTime.to_municipality_id == office.municipality_id)
            .order_by(Municipality.code)
        )
        return [
            RouteRow(group, name, minutes, None, None) for group, name, minutes in rows
        ]
    confirmed = db.session.execute(
        select(
            Municipality.region_group,
            Municipality.name,
            OfficeRoute.minutes,
            OfficeRoute.confirmed_at,
            User.display_name,
        )
        .join(OfficeRoute, OfficeRoute.from_municipality_id == Municipality.id)
        .join(User, User.id == OfficeRoute.confirmed_by)
        .where(OfficeRoute.office_id == office.id)
        .order_by(Municipality.code)
    )
    return [
        RouteRow(group, name, minutes, confirmed_at, confirmed_by)
        for group, name, minutes, confirmed_at, confirmed_by in confirmed
    ]


def save_route(
    office_id: int, actor_id: int, municipality_id: int | None, raw_minutes: str | None
) -> None:
    """確かめた経路を1つ登録する。同じ町がすでにあれば、分を直す。"""
    text = (raw_minutes or "").strip().translate(FULLWIDTH_DIGITS)
    if not (text.isascii() and text.isdigit() and len(text) <= 3):
        raise RouteError(MINUTES_ERROR)
    minutes = int(text)
    if not 1 <= minutes <= MINUTES_MAX:
        raise RouteError(MINUTES_ERROR)
    office = db.session.scalar(
        select(Office)
        .where(Office.id == office_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if office is None or not office.is_active:
        db.session.rollback()
        raise RouteOfficeNotFound
    if office.is_demo:
        db.session.rollback()
        raise RouteConflict(
            "デモの事務所は、架空の移動時間の表を使います。登録はできません。"
        )
    # 桁の多い値は、DB に渡す前に落とす。
    known = municipality_id is not None and 1 <= municipality_id <= ID_MAX
    town = db.session.get(Municipality, municipality_id) if known else None
    if not allowed(town, demo=False):
        db.session.rollback()
        raise RouteError("出発の市町村を選んでください。")
    assert town is not None
    route = db.session.scalar(
        select(OfficeRoute)
        .where(
            OfficeRoute.office_id == office.id,
            OfficeRoute.from_municipality_id == town.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if route is None:
        route = OfficeRoute(office_id=office.id, from_municipality_id=town.id)
        db.session.add(route)
    route.minutes = minutes
    route.confirmed_by = actor_id
    route.confirmed_at = utc_now()
    try:
        db.session.flush()
        record_audit(
            "office_route_saved",
            actor_id=actor_id,
            office_id=office.id,
            target_type="office_route",
            target_id=route.id,
        )
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise RouteConflict("保存できませんでした。もう一度お試しください。") from None
