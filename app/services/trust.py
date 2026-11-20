from dataclasses import dataclass
from datetime import date, timedelta
from secrets import token_hex

from flask import abort, current_app
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import (
    BookkeepingLevel,
    Engagement,
    Role,
    Talent,
    TalentAffiliation,
    TalentSkill,
    TalentSkillKind,
    TrustItem,
    TrustKind,
    User,
    utc_now,
)
from app.services.billing import protect_payout
from tsunagu_rules.types import NeedKind


@dataclass(frozen=True)
class TrustStatus:
    kind: TrustKind
    valid_from: date | None = None
    expires_on: date | None = None
    days_left: int | None = None

    @property
    def valid(self) -> bool:
        return self.days_left is not None


def trust_statuses(talent: Talent, today: date) -> tuple[TrustStatus, ...]:
    items = list(
        db.session.scalars(
            select(TrustItem)
            .where(TrustItem.talent_id == talent.id)
            .order_by(TrustItem.expires_on.desc(), TrustItem.id.desc())
        )
    )
    statuses = []
    for kind in TrustKind:
        matching = [item for item in items if item.kind == kind]
        active = next(
            (item for item in matching if item.valid_from <= today <= item.expires_on),
            None,
        )
        if active is not None:
            statuses.append(
                TrustStatus(
                    kind,
                    active.valid_from,
                    active.expires_on,
                    (active.expires_on - today).days,
                )
            )
        elif matching:
            latest = matching[0]
            statuses.append(TrustStatus(kind, latest.valid_from, latest.expires_on))
        else:
            statuses.append(TrustStatus(kind))
    return tuple(statuses)


@dataclass(frozen=True)
class ProfileInput:
    municipality_id: int
    grade: BookkeepingLevel
    weekdays_mask: int
    affiliation: TalentAffiliation
    capacity: int
    is_listed: bool
    tools: frozenset[str]
    strengths: frozenset[NeedKind]
    payout: str | None = None
    registration: str | None = None


class TrustConflict(Exception):
    pass


PLEDGE_VERSION = "1.0"
PLEDGE_TERMS = (
    "業務で知った事務所や顧問先の秘密を第三者に漏らしません。",
    "顧問先の生データを事務所の外へ持ち出しません。",
    "税務代理・税務書類の作成・税務相談は行いません。",
)


def _commit() -> None:
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise TrustConflict("保存できませんでした。内容をご確認ください。") from None


def save_profile(user_id: int, data: ProfileInput) -> None:
    user = db.session.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None or not user.is_active or user.role != Role.TSUNAGITE:
        abort(404)
    talent = db.session.scalar(
        select(Talent)
        .where(Talent.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if talent is None:
        talent = Talent(user_id=user_id)
        db.session.add(talent)
    if talent.bookkeeping_level != data.grade:
        talent.level_verified_at = None
        talent.level_verified_by = None
    if data.payout is not None:
        talent.payout_cipher = protect_payout(data.payout) if data.payout else None
    if data.registration is not None:
        talent.invoice_registration = data.registration or None
    talent.bookkeeping_level = data.grade
    talent.municipality_id = data.municipality_id
    talent.weekdays_mask = data.weekdays_mask
    talent.affiliation = data.affiliation
    talent.capacity = data.capacity
    talent.is_listed = data.is_listed
    requested = {
        *((TalentSkillKind.TOOL, tool) for tool in data.tools),
        *((TalentSkillKind.STRENGTH, kind.value) for kind in data.strengths),
    }
    if talent.id is None:
        try:
            db.session.flush()
        except DataError, IntegrityError, OperationalError:
            db.session.rollback()
            raise TrustConflict("プロフィールを保存できませんでした。") from None
        retained: set[tuple[TalentSkillKind, str]] = set()
    else:
        retained = set()
        with db.session.no_autoflush:
            for skill in db.session.scalars(
                select(TalentSkill)
                .where(TalentSkill.talent_id == talent.id)
                .with_for_update()
            ):
                pair = (skill.kind, skill.tag)
                if pair in requested:
                    retained.add(pair)
                else:
                    db.session.delete(skill)
    db.session.add_all(
        TalentSkill(talent_id=talent.id, kind=kind, tag=tag)
        for kind, tag in sorted(requested - retained)
    )
    record_audit(
        "talent_profile_saved",
        actor_id=user_id,
        target_type="talent",
        target_id=talent.id,
    )
    _commit()


def _locked_talent(talent_id: int) -> Talent:
    talent = db.session.get(
        Talent, talent_id, with_for_update=True, populate_existing=True
    )
    if talent is None:
        abort(404)
    return talent


def add_trust_record(talent_id: int, kind: TrustKind, actor_id: int, day: date) -> None:
    talent = _locked_talent(talent_id)
    if kind == TrustKind.PLEDGE and talent.user_id != actor_id:
        abort(404)
    version = PLEDGE_VERSION if kind == TrustKind.PLEDGE else None
    exists = db.session.scalar(
        select(TrustItem.id)
        .where(
            TrustItem.talent_id == talent_id,
            TrustItem.kind == kind,
            TrustItem.valid_from == day,
            TrustItem.pledge_version == version,
        )
        .limit(1)
        .with_for_update()
    )
    if exists is not None:
        return
    db.session.add(
        TrustItem(
            talent_id=talent_id,
            kind=kind,
            valid_from=day,
            expires_on=day + timedelta(days=current_app.config["PLEDGE_VALID_DAYS"]),
            recorded_by=actor_id,
            pledge_version=version,
        )
    )
    record_audit(
        "pledge_signed" if kind == TrustKind.PLEDGE else "training_recorded",
        actor_id=actor_id,
        target_type="talent",
        target_id=talent_id,
    )
    _commit()


TRANSLATION_CRITERIA = {
    "missing": "不足している資料を特定した",
    "question": "相手が答えられる確認の一文を書いた",
    "record": "事務所の言葉で次の対応を記録した",
}


def verify_translation(talent_id: int, actor_id: int, criteria: set[str]) -> None:
    talent = _locked_talent(talent_id)
    if criteria != TRANSLATION_CRITERIA.keys():
        raise TrustConflict("3つの採点項目をすべて確認してください。")
    if talent.translation_verified_at is None:
        talent.translation_verified_at = utc_now()
        talent.translation_verified_by = actor_id
        record_audit(
            "translation_passed",
            actor_id=actor_id,
            target_type="talent",
            target_id=talent_id,
        )
        _commit()


def verify_skill(
    engagement: Engagement, skill_id: int, actor_id: int, expected_tag: str
) -> None:
    _locked_talent(engagement.talent_id)
    skill = db.session.scalar(
        select(TalentSkill)
        .where(
            TalentSkill.id == skill_id, TalentSkill.talent_id == engagement.talent_id
        )
        .with_for_update()
    )
    if skill is None:
        abort(404)
    if skill.tag != expected_tag:
        raise TrustConflict("自己申告が変わりました。現在の内容を確認してください。")
    if skill.verified_at is None:
        skill.verified_at = utc_now()
        skill.verified_by = actor_id
        skill.verified_office_id = engagement.office_id
        record_audit(
            "skill_verified",
            actor_id=actor_id,
            office_id=engagement.office_id,
            target_type="talent_skill",
            target_id=skill.id,
        )
        _commit()


def verify_grade(talent_id: int, expected: BookkeepingLevel, actor_id: int) -> None:
    talent = _locked_talent(talent_id)
    if expected == BookkeepingLevel.NONE or talent.bookkeeping_level != expected:
        raise TrustConflict(
            "表示後に簿記の級が変わりました。現在の内容をご確認ください。"
        )
    if talent.level_verified_at is not None:
        return
    talent.level_verified_at = utc_now()
    talent.level_verified_by = actor_id
    record_audit(
        "bookkeeping_verified",
        actor_id=actor_id,
        target_type="talent",
        target_id=talent_id,
    )
    _commit()


def suspend_user(user_id: int, actor_id: int) -> None:
    user = db.session.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None:
        abort(404)
    if user.role == Role.ADMIN:
        abort(403)
    if not user.is_active:
        return
    user.is_active = False
    user.session_nonce = token_hex(16)
    record_audit(
        "user_suspended", actor_id=actor_id, target_type="user", target_id=user_id
    )
    _commit()
