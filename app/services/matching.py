from dataclasses import dataclass
from datetime import date, timedelta

from flask import abort, current_app
from sqlalchemy import Select, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.authz import require_need
from app.extensions import db
from app.models import (
    CandidateStatus,
    Engagement,
    EngagementStatus,
    MatchCandidate,
    MatchRun,
    Need,
    NeedStatus,
    Office,
    Talent,
    TalentSkill,
    TalentSkillKind,
    TravelTime,
    TrustKind,
    User,
    today_in_japan,
    utc_now,
)
from app.services.ledger import append_record
from app.services.mail import notify_office, notify_user
from app.services.needs import translations
from app.services.statement import confirmed_visits
from app.services.trust import trust_statuses
from tsunagu_rules import RULES_VERSION
from tsunagu_rules.matching import match
from tsunagu_rules.types import (
    MatchLimits,
    MatchResult,
    NeedKind,
)
from tsunagu_rules.types import (
    Need as RuleNeed,
)
from tsunagu_rules.types import (
    Talent as RuleTalent,
)


class MatchConflict(Exception):
    pass


@dataclass(frozen=True)
class Offer:
    weekday: int
    share_bp: int
    cap_yen: int | None


def _days(mask: int) -> frozenset[int]:
    return frozenset(day for day in range(7) if mask & (1 << day))


def _candidate_query(need_id: int) -> Select[MatchCandidate]:
    return select(MatchCandidate).join(MatchRun).where(MatchRun.need_id == need_id)


def latest_run(need: Need) -> MatchRun | None:
    return db.session.scalar(
        select(MatchRun)
        .where(MatchRun.need_id == need.id)
        .order_by(MatchRun.id.desc())
        .limit(1)
    )


def _rank(
    need: Need, office: Office, today: date, only: int | None = None
) -> MatchResult:
    declined = db.session.scalars(
        _candidate_query(need.id)
        .with_only_columns(MatchCandidate.talent_id)
        .where(MatchCandidate.status == CandidateStatus.DECLINED)
    )
    request = RuleNeed(
        str(office.municipality_id),
        _days(need.weekdays_mask),
        frozenset(row.category for row in translations(need)),
        office.software or "",
        frozenset(declined),
    )
    routes = {
        (str(row.from_municipality_id), str(row.to_municipality_id)): row.minutes
        for row in db.session.scalars(
            select(TravelTime).where(
                TravelTime.to_municipality_id == office.municipality_id
            )
        )
    }
    routes[request.municipality, request.municipality] = 15
    query = (
        select(Talent, User)
        .join(User, User.id == Talent.user_id)
        .where(User.is_demo.is_(office.is_demo))
    )
    if only is not None:
        query = query.where(Talent.id == only)
    pool = []
    for talent, user in db.session.execute(query):
        skills = list(
            db.session.scalars(
                select(TalentSkill).where(
                    TalentSkill.talent_id == talent.id,
                    TalentSkill.verified_at.is_not(None),
                )
            )
        )
        active_query = select(Engagement.id).where(
            Engagement.talent_id == talent.id,
            Engagement.status != EngagementStatus.ENDED,
        )
        if only is not None:
            active_query = active_query.with_for_update()
        active = db.session.scalars(active_query).all()
        expiry = {
            item.kind: item.expires_on
            for item in trust_statuses(talent, today)
            if item.valid
        }
        grade = talent.bookkeeping_level.value
        pool.append(
            RuleTalent(
                talent.id,
                str(talent.municipality_id),
                _days(talent.weekdays_mask),
                frozenset(
                    NeedKind(skill.tag)
                    for skill in skills
                    if skill.kind == TalentSkillKind.STRENGTH
                ),
                frozenset(
                    skill.tag for skill in skills if skill.kind == TalentSkillKind.TOOL
                ),
                expiry.get(TrustKind.PLEDGE),
                expiry.get(TrustKind.TRAINING),
                len(active),
                talent.capacity,
                talent.is_listed,
                not user.is_active,
                int(grade) if talent.level_verified_at and grade != "none" else None,
                confirmed_visits(talent.id),
                talent.translation_verified_at is not None,
            )
        )
    return match(
        request,
        pool,
        routes,
        today,
        MatchLimits(current_app.config.get("TRAVEL_LIMIT_MINUTES", 60)),
    )


def _commit() -> None:
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise MatchConflict(
            "変更を保存できませんでした。もう一度お試しください。"
        ) from None


def _locked_candidates(need: Need) -> list[MatchCandidate]:
    return list(
        db.session.scalars(
            _candidate_query(need.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def generate(office: Office, need_id: int, actor_id: int) -> None:
    need = require_need(office, need_id, lock=True)
    if need.status != NeedStatus.PUBLISHED:
        raise MatchConflict("公開中の困りごとだけ候補を出せます。")
    candidates = _locked_candidates(need)
    if any(row.status == CandidateStatus.INVITED for row in candidates):
        raise MatchConflict("声かけの回答を待ってから候補を出し直してください。")
    result = _rank(need, office, today_in_japan())
    run = MatchRun(
        need_id=need.id,
        rules_version=RULES_VERSION,
        input_hash=need.input_hash or "",
        excluded_summary={
            reason.value: count for reason, count in result.excluded.items()
        },
        run_by=actor_id,
    )
    db.session.add(run)
    for row in candidates:
        if row.status == CandidateStatus.LISTED:
            row.status = CandidateStatus.CLOSED
    try:
        db.session.flush()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise MatchConflict("候補を保存できませんでした。") from None
    db.session.add_all(
        MatchCandidate(
            match_id=run.id,
            talent_id=candidate.talent_id,
            rank=index,
            score=candidate.score,
            reasons=list(candidate.reasons),
        )
        for index, candidate in enumerate(result.candidates, start=1)
    )
    _commit()


def invite(
    office: Office, need_id: int, candidate_id: int, actor_id: int, offer: Offer
) -> None:
    need = require_need(office, need_id, lock=True)
    candidates = _locked_candidates(need)
    candidate = next((row for row in candidates if row.id == candidate_id), None)
    if candidate is None:
        abort(404)
    if (
        need.status != NeedStatus.PUBLISHED
        or candidate.status != CandidateStatus.LISTED
    ):
        raise MatchConflict("この候補には声をかけられません。")
    if any(row.status == CandidateStatus.INVITED for row in candidates):
        raise MatchConflict("すでに回答を待っている声かけがあります。")
    talent = db.session.get(Talent, candidate.talent_id, with_for_update=True)
    limit = min(current_app.config.get("FEE_SHARE_MAX_BP", 4999), 4999)
    if not 1 <= offer.share_bp <= limit or (
        offer.cap_yen is not None and offer.cap_yen < 0
    ):
        raise MatchConflict("料金の条件を確認してください。")
    if talent is None or offer.weekday not in _days(
        need.weekdays_mask & talent.weekdays_mask
    ):
        raise MatchConflict("双方が通える曜日を選んでください。")
    if not _rank(need, office, today_in_japan(), talent.id).candidates:
        raise MatchConflict("候補の条件が変わりました。候補を出し直してください。")
    candidate.status = CandidateStatus.INVITED
    candidate.offer_weekday = offer.weekday
    candidate.offer_fee_share_bp = offer.share_bp
    candidate.offer_fee_cap_yen = offer.cap_yen
    candidate.invited_by = actor_id
    candidate.invited_at = utc_now()
    append_record(
        need,
        "invited",
        actor_id,
        {
            "talent_id": talent.id,
            "weekday": offer.weekday,
            "fee_share_bp": offer.share_bp,
            "fee_cap_yen": offer.cap_yen,
        },
    )
    record_audit(
        "invited",
        actor_id=actor_id,
        office_id=office.id,
        target_type="need",
        target_id=need.id,
    )
    with db.session.no_autoflush:
        recipient = db.session.get(User, talent.user_id)
    if recipient is not None:
        notify_user(
            recipient,
            "伴走の声かけが届きました",
            "つなぎ手のホームで声かけを確認してください。",
        )
    _commit()


def cancel(office: Office, need_id: int, candidate_id: int, actor_id: int) -> None:
    need = require_need(office, need_id, lock=True)
    candidate = next(
        (row for row in _locked_candidates(need) if row.id == candidate_id), None
    )
    if candidate is None:
        abort(404)
    if (
        need.status != NeedStatus.PUBLISHED
        or candidate.status != CandidateStatus.INVITED
    ):
        raise MatchConflict("取り消せる声かけがありません。")
    candidate.status = CandidateStatus.CLOSED
    candidate.responded_at = utc_now()
    append_record(need, "changed", actor_id, {"cancelled_candidate_id": candidate.id})
    _commit()


def respond(candidate_id: int, actor_id: int, *, accept: bool) -> int | None:
    need_id = db.session.scalar(
        select(MatchRun.need_id)
        .join(MatchCandidate)
        .join(Talent)
        .where(MatchCandidate.id == candidate_id, Talent.user_id == actor_id)
    )
    if need_id is None:
        abort(404)
    need = db.session.get(Need, need_id, with_for_update=True, populate_existing=True)
    if need is None:
        abort(404)
    candidates = _locked_candidates(need)
    candidate = next((row for row in candidates if row.id == candidate_id), None)
    if (
        candidate is None
        or need.status != NeedStatus.PUBLISHED
        or candidate.status != CandidateStatus.INVITED
    ):
        abort(404)
    candidate.responded_at = utc_now()
    if not accept:
        candidate.status = CandidateStatus.DECLINED
        _commit()
        return None

    talent = db.session.get(Talent, candidate.talent_id, with_for_update=True)
    office = db.session.get(Office, need.office_id, with_for_update=True)
    today = today_in_japan()
    weekday, share = candidate.offer_weekday, candidate.offer_fee_share_bp
    if (
        talent is None
        or office is None
        or not office.is_active
        or weekday is None
        or share is None
        or weekday not in _days(need.weekdays_mask & talent.weekdays_mask)
        or not _rank(need, office, today, talent.id).candidates
    ):
        raise MatchConflict("伴走の条件が変わりました。事務所にご確認ください。")
    engagement = Engagement(
        office_id=office.id,
        need_id=need.id,
        talent_id=talent.id,
        candidate_id=candidate.id,
        weekday=weekday,
        hourly_rate_yen=office.hourly_rate_yen,
        fee_share_bp=share,
        fee_cap_yen=candidate.offer_fee_cap_yen,
        trial_ends_on=today + timedelta(days=current_app.config.get("TRIAL_DAYS", 30)),
        agreed_at=candidate.responded_at,
    )
    db.session.add(engagement)
    for row in candidates:
        if row.status in {CandidateStatus.LISTED, CandidateStatus.INVITED}:
            row.status = CandidateStatus.CLOSED
    candidate.status = CandidateStatus.ACCEPTED
    need.status = NeedStatus.ENGAGED
    append_record(
        need,
        "agreed",
        actor_id,
        {
            "talent_id": talent.id,
            "weekday": weekday,
            "fee_share_bp": share,
            "fee_cap_yen": engagement.fee_cap_yen,
            "hourly_rate_yen": engagement.hourly_rate_yen,
        },
    )
    record_audit(
        "agreed",
        actor_id=actor_id,
        office_id=office.id,
        target_type="need",
        target_id=need.id,
    )
    notify_office(
        office.id,
        "伴走が決まりました",
        "事務所のホームで合意の内容を確認してください。",
    )
    with db.session.no_autoflush:
        recipient = db.session.get(User, actor_id)
    if recipient is not None:
        notify_user(
            recipient,
            "伴走が決まりました",
            "つなぎ手のホームで合意の内容を確認してください。",
        )
    _commit()
    return engagement.id
