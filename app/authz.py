from typing import cast

from flask import abort
from flask_login import current_user
from sqlalchemy import select

from app.audit import record_audit
from app.extensions import db
from app.models import (
    MemberRole,
    Municipality,
    MunicipalityMember,
    Need,
    Office,
    OfficeMember,
    Role,
    User,
)


def require_role(role: Role) -> User:
    user = cast(User, current_user)
    if user.role != role:
        abort(403)
    return user


def require_office(
    office_id: int, *, owner_only: bool = False, write: bool = False
) -> Office:
    user = cast(User, current_user)
    offices = select(Office).where(Office.id == office_id, Office.is_active.is_(True))
    if user.role == Role.ADMIN:
        office = db.session.scalar(offices)
        if office is None:
            abort(404)
        if owner_only or write:
            abort(403)
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
    user = require_role(Role.MUNICIPALITY)
    municipality = db.session.scalar(
        select(Municipality)
        .join(MunicipalityMember)
        .where(Municipality.code == code, MunicipalityMember.user_id == user.id)
    )
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
