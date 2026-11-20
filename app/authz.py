from typing import cast

from flask import abort
from flask_login import current_user
from sqlalchemy import select

from app.audit import record_audit
from app.extensions import db
from app.models import (
    Engagement,
    MemberRole,
    Municipality,
    MunicipalityMember,
    Need,
    Office,
    OfficeMember,
    Role,
    Talent,
    User,
)


def require_role(role: Role) -> User:
    user = cast(User, current_user)
    if user.role != role:
        abort(403)
    return user


def require_office(
    office_id: int, *, owner_only: bool = False, write: bool = False, audit: bool = True
) -> Office:
    user = cast(User, current_user)
    offices = select(Office).where(Office.id == office_id, Office.is_active.is_(True))
    if user.role == Role.ADMIN:
        office = db.session.scalar(offices)
        if office is None:
            abort(404)
        if owner_only or write:
            abort(403)
        if audit:
            record_audit("admin_viewed_office", actor_id=user.id, office_id=office.id)
            db.session.commit()
        return office
    if user.role != Role.OFFICE:
        abort(404)
    result = db.session.execute(
        offices.add_columns(OfficeMember.member_role)
        .join(OfficeMember, OfficeMember.office_id == Office.id)
        .where(OfficeMember.user_id == user.id)
    ).one_or_none()
    if result is None:
        abort(404)
    office, member_role = result
    if owner_only and member_role != MemberRole.OWNER:
        abort(403)
    return cast(Office, office)


def require_municipality(code: str) -> Municipality:
    user = cast(User, current_user)
    query = select(Municipality).where(Municipality.code == code)
    if user.role == Role.MUNICIPALITY:
        query = query.join(MunicipalityMember).where(
            MunicipalityMember.user_id == user.id
        )
    elif user.role != Role.ADMIN:
        abort(404)
    municipality = db.session.scalar(query)
    if municipality is None:
        abort(404)
    return municipality


def require_need(office: Office, need_id: int, *, lock: bool = False) -> Need:
    query = select(Need).where(Need.office_id == office.id, Need.id == need_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    need = db.session.scalar(query)
    if need is None:
        abort(404)
    return need


def require_need_office(
    office_id: int, need_id: int, *, owner_only: bool = False
) -> Office:
    office = require_office(office_id, audit=False)
    require_need(office, need_id)
    return require_office(office_id, write=True, owner_only=owner_only)


def require_office_engagement(
    office_id: int, engagement_id: int, *, write: bool = False, owner_only: bool = False
) -> Engagement:
    user = cast(User, current_user)
    if (write or owner_only) and user.role == Role.TSUNAGITE:
        assigned = db.session.scalar(
            select(Engagement.id)
            .join(Talent)
            .join(Office, Office.id == Engagement.office_id)
            .where(
                Engagement.office_id == office_id,
                Engagement.id == engagement_id,
                Talent.user_id == user.id,
                Office.is_active.is_(True),
            )
        )
        abort(403 if assigned is not None else 404)
    office = require_office(office_id, audit=not (write or owner_only))
    engagement = db.session.scalar(
        select(Engagement).where(
            Engagement.office_id == office.id,
            Engagement.id == engagement_id,
        )
    )
    if engagement is None:
        abort(404)
    if write or owner_only:
        require_office(office_id, write=write, owner_only=owner_only)
    return engagement


def require_talent_engagement(engagement_id: int, *, write: bool = False) -> Engagement:
    user = cast(User, current_user)
    if write and user.role == Role.ADMIN:
        abort(403)
    if user.role != Role.TSUNAGITE:
        abort(404)
    engagement = db.session.scalar(
        select(Engagement)
        .join(Talent)
        .where(
            Engagement.id == engagement_id,
            Talent.user_id == user.id,
        )
    )
    if engagement is None:
        abort(404)
    return engagement
