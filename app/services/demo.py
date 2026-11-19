from datetime import date, timedelta
from typing import Any

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import (
    AuditLog,
    BookkeepingLevel,
    CandidateStatus,
    Engagement,
    Invitation,
    ItTask,
    LedgerRecord,
    MatchCandidate,
    MatchRun,
    MemberRole,
    Municipality,
    MunicipalityMember,
    Need,
    NeedStatus,
    NeedTranslation,
    Office,
    OfficeMember,
    OutboxMail,
    Reduction,
    ReductionStatus,
    Role,
    Statement,
    StatementStatus,
    Talent,
    TalentAffiliation,
    TalentSkill,
    TalentSkillKind,
    TranslationSource,
    TravelTime,
    TrustItem,
    TrustKind,
    User,
    Visit,
    VisitStatus,
    today_in_japan,
    utc_now,
)
from app.services.ledger import append_record
from app.services.needs import DICTIONARY
from tsunagu_rules import RULES_VERSION
from tsunagu_rules.translation import translate
from tsunagu_rules.types import JsonValue, NeedKind
from tsunagu_rules.types import Reduction as RuleReduction
from tsunagu_rules.value import calc_fee

ACCOUNTS = {
    Role.OFFICE: "office@tsunagu.example",
    Role.TSUNAGITE: "talent1@tsunagu.example",
    Role.MUNICIPALITY: "region@tsunagu.example",
}
SCRIPT_BODY = (
    "顧問先の資料が月末まで集まらない。試算表を過去と見比べるのに、毎月30時間かかる"
)


class DemoError(ValueError):
    pass


def demo_allowed() -> bool:
    return current_app.config["APP_ENV"] in {"demo", "development"}


def demo_password() -> str:
    password = str(current_app.config["DEMO_PASSWORD"])
    if len(password) < 12:
        raise DemoError("DEMO_PASSWORD に12文字以上の合言葉を設定してください。")
    return password


def _add[T](row: T) -> T:
    db.session.add(row)
    db.session.flush()
    return row


def _town(code: str, name: str) -> Municipality:
    town = db.session.scalar(select(Municipality).where(Municipality.code == code))
    if town is None:
        return _add(Municipality(code=code, name=name, region_group="架空広域"))
    if town.name != name or town.region_group != "架空広域":
        raise DemoError("架空の市町村コードが既存のデータと重複しています。")
    return town


def _user(email: str, name: str, role: Role, password: str) -> User:
    user = User(email=email, display_name=name, role=role, is_demo=True)
    user.set_password(password)
    return _add(user)


def _need(office: Office, owner: User, body: str) -> Need:
    translated = translate(body, DICTIONARY)
    need = _add(
        Need(
            office_id=office.id,
            created_by=owner.id,
            body=body,
            monthly_hours=30,
            weekdays_mask=2,
            status=NeedStatus.BLOCKED if translated.blocked else NeedStatus.DRAFT,
            block_reason="・".join(translated.blocked_words) or None,
            input_hash=translated.input_hash,
            rules_version=RULES_VERSION,
        )
    )
    db.session.add_all(
        NeedTranslation(
            need_id=need.id,
            category=pattern.kind,
            it_task=ItTask(task),
            evidence="・".join(pattern.words),
            source=TranslationSource.RULE,
        )
        for pattern in translated.patterns
        for task in pattern.tasks
    )
    return need


def _example(
    office: Office, owner: User, talent: Talent, today: date, *, finished: bool
) -> None:
    need = _need(office, owner, SCRIPT_BODY)
    need.status = NeedStatus.ENGAGED
    need.published_at = utc_now()
    need.published_by = owner.id
    need.value_yen_month = 120000
    run = _add(
        MatchRun(
            need_id=need.id,
            rules_version=RULES_VERSION,
            input_hash=need.input_hash or "",
            excluded_summary={},
            run_by=owner.id,
        )
    )
    candidate = _add(
        MatchCandidate(
            match_id=run.id,
            talent_id=talent.id,
            rank=1,
            score=85,
            reasons=["車で15分：毎週通える距離です"],
            status=CandidateStatus.ACCEPTED,
            offer_weekday=1,
            offer_fee_share_bp=4000,
            invited_by=owner.id,
            invited_at=utc_now(),
            responded_at=utc_now(),
        )
    )
    engagement = _add(
        Engagement(
            office_id=office.id,
            need_id=need.id,
            talent_id=talent.id,
            candidate_id=candidate.id,
            weekday=1,
            hourly_rate_yen=4000,
            fee_share_bp=4000,
            trial_ends_on=today + timedelta(days=30),
            agreed_at=utc_now(),
        )
    )
    events: tuple[tuple[str, int, dict[str, JsonValue]], ...] = (
        ("published", owner.id, {"value_yen_month": 120000}),
        ("invited", owner.id, {"weekday": 1, "fee_share_bp": 4000}),
        (
            "agreed",
            talent.user_id,
            {"weekday": 1, "fee_share_bp": 4000, "hourly_rate_yen": 4000},
        ),
    )
    for kind, actor, data in events:
        append_record(need, kind, actor, data)
        db.session.flush()
    for week in range(4):
        day = today - timedelta(days=7 * week)
        visit = _add(
            Visit(
                engagement_id=engagement.id,
                visit_date=day,
                minutes=60,
                category=NeedKind.COLLECT,
                status=VisitStatus.CONFIRMED,
                confirmed_by=owner.id,
                confirmed_at=utc_now(),
            )
        )
        append_record(
            need,
            "visit_confirmed",
            owner.id,
            {
                "visit_id": visit.id,
                "visit_date": day.isoformat(),
                "category": "collect",
                "minutes": 60,
            },
        )
        db.session.flush()
    month = today.strftime("%Y-%m")
    db.session.add(
        Reduction(
            engagement_id=engagement.id,
            month=month,
            category=NeedKind.COLLECT,
            before_hours=30,
            after_hours=10,
            status=ReductionStatus.CONFIRMED,
            confirmed_by=owner.id,
            confirmed_at=utc_now(),
        )
    )
    append_record(
        need,
        "reduction_confirmed",
        owner.id,
        {
            "month": month,
            "category": "collect",
            "before_hours": 30,
            "after_hours": 10,
        },
    )
    db.session.flush()
    if finished:
        fee = calc_fee([RuleReduction(30, 10, True)], 4000, 4000, None)
        db.session.add(
            Statement(
                engagement_id=engagement.id,
                month=month,
                reduced_hours=fee.reduced_hours,
                hourly_rate_yen=4000,
                fee_share_bp=4000,
                value_yen=fee.value_yen,
                fee_yen=fee.fee_yen,
                waived=fee.waived,
                rules_version=RULES_VERSION,
                status=StatementStatus.FINALIZED,
                finalized_by=owner.id,
                finalized_at=utc_now(),
            )
        )
        append_record(
            need,
            "statement_finalized",
            owner.id,
            {
                "month": month,
                "reduced_hours": fee.reduced_hours,
                "value_yen": fee.value_yen,
                "fee_yen": fee.fee_yen,
                "waived": fee.waived,
            },
        )
        db.session.flush()


def seed_demo(password: str) -> bool:
    existing = db.session.scalar(
        select(User).where(User.email == ACCOUNTS[Role.OFFICE])
    )
    if existing is not None:
        if existing.is_demo:
            return False
        raise DemoError("デモのアカウント名が既存のデータと重複しています。")
    today = today_in_japan()
    south, north, east = (
        _town("90001", "みなみ市"),
        _town("90002", "きた町"),
        _town("90003", "ひがし村"),
    )
    for origin, destination, minutes in (
        (north, south, 25),
        (east, south, 70),
        (south, north, 25),
        (south, east, 70),
        (north, east, 50),
        (east, north, 50),
    ):
        if (
            db.session.scalar(
                select(TravelTime.id).where(
                    TravelTime.from_municipality_id == origin.id,
                    TravelTime.to_municipality_id == destination.id,
                )
            )
            is None
        ):
            db.session.add(
                TravelTime(
                    from_municipality_id=origin.id,
                    to_municipality_id=destination.id,
                    minutes=minutes,
                )
            )
    offices = [
        _add(
            Office(
                name=f"架空会計事務所 {index + 1}",
                municipality_id=south.id if index < 6 else north.id,
                software="excel",
                is_demo=True,
            )
        )
        for index in range(8)
    ]
    owner = _user(ACCOUNTS[Role.OFFICE], "デモ事務所の代表", Role.OFFICE, password)
    db.session.add(
        OfficeMember(
            office_id=offices[0].id, user_id=owner.id, member_role=MemberRole.OWNER
        )
    )
    regional = _user(
        ACCOUNTS[Role.MUNICIPALITY], "デモ自治体の担当", Role.MUNICIPALITY, password
    )
    db.session.add(MunicipalityMember(user_id=regional.id, municipality_id=south.id))
    recorder = _user("recorder@tsunagu.example", "デモの記録担当", Role.ADMIN, password)
    # This account only attributes seeded records and cannot sign in.
    recorder.is_active = False
    talents = []
    for index in range(1, 9):
        user = _user(
            f"talent{index}@tsunagu.example",
            f"架空つなぎ手 {index}",
            Role.TSUNAGITE,
            password,
        )
        town = east if index == 6 else north if index == 8 else south
        grade = BookkeepingLevel.SECOND if index == 1 else BookkeepingLevel.THIRD
        talent = _add(
            Talent(
                user_id=user.id,
                municipality_id=town.id,
                bookkeeping_level=grade,
                level_verified_at=utc_now() if index < 5 else None,
                level_verified_by=recorder.id if index < 5 else None,
                weekdays_mask=1 if index == 7 else 2,
                capacity=2,
                affiliation=TalentAffiliation.KYORYOKUTAI
                if index <= 5
                else TalentAffiliation.GRADUATE,
                is_listed=True,
            )
        )
        talents.append(talent)
        db.session.add(
            TalentSkill(
                talent_id=talent.id,
                kind=TalentSkillKind.TOOL,
                tag="excel" if index == 1 else "sheets",
            )
        )
        for kind in TrustKind:
            expires = (
                today - timedelta(days=1)
                if index == 5 and kind == TrustKind.PLEDGE
                else today + timedelta(days=180)
            )
            db.session.add(
                TrustItem(
                    talent_id=talent.id,
                    kind=kind,
                    valid_from=expires - timedelta(days=365),
                    expires_on=expires,
                    recorded_by=recorder.id,
                )
            )
    _need(offices[0], owner, SCRIPT_BODY)
    _need(offices[0], owner, "申告書の作成も頼みたい")
    _example(offices[0], owner, talents[0], today, finished=False)
    for index in (1, 2):
        example_owner = _user(
            f"example{index}@tsunagu.example",
            f"デモ事務所の代表 {index + 1}",
            Role.OFFICE,
            password,
        )
        db.session.add(
            OfficeMember(
                office_id=offices[index].id,
                user_id=example_owner.id,
                member_role=MemberRole.OWNER,
            )
        )
        _example(offices[index], example_owner, talents[7], today, finished=True)
    return True


def clear_demo() -> None:
    # SQLite does not enable foreign keys by default. Enforce them before the
    # first write so references from outside the demo cause a full rollback.
    connection = db.session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    users = select(User.id).where(User.is_demo.is_(True))
    offices = select(Office.id).where(Office.is_demo.is_(True))
    needs = select(Need.id).where(Need.office_id.in_(offices))
    talents = select(Talent.id).where(Talent.user_id.in_(users))
    runs = select(MatchRun.id).where(MatchRun.need_id.in_(needs))
    engagements = select(Engagement.id).where(
        Engagement.need_id.in_(needs), Engagement.office_id.in_(offices)
    )
    cross_references = (
        select(OfficeMember.id).where(
            OfficeMember.office_id.in_(offices) != OfficeMember.user_id.in_(users)
        ),
        select(Need.id).where(
            Need.office_id.in_(offices), Need.created_by.not_in(users)
        ),
        select(Engagement.id).where(
            Engagement.office_id.in_(offices) != Engagement.talent_id.in_(talents)
        ),
        select(AuditLog.id).where(
            AuditLog.actor_user_id.in_(users),
            AuditLog.office_id.is_not(None),
            AuditLog.office_id.not_in(offices),
        ),
        select(Talent.id).where(
            Talent.user_id.not_in(users), Talent.level_verified_by.in_(users)
        ),
    )
    if any(db.session.scalar(query.limit(1)) is not None for query in cross_references):
        raise DemoError(
            "デモと通常のデータをまたぐ記録があるため初期化を中止しました。"
        )
    demo_addresses = select(User.email).where(User.is_demo.is_(True))
    invitation_addresses = select(Invitation.email).where(Invitation.is_demo.is_(True))
    groups: list[tuple[type[Any], Any]] = [
        (
            OutboxMail,
            OutboxMail.is_demo.is_(True)
            & (
                OutboxMail.recipient.in_(demo_addresses)
                | OutboxMail.recipient.in_(invitation_addresses)
            ),
        ),
        (Invitation, Invitation.is_demo.is_(True)),
        (Statement, Statement.engagement_id.in_(engagements)),
        (Reduction, Reduction.engagement_id.in_(engagements)),
        (Visit, Visit.engagement_id.in_(engagements)),
        (LedgerRecord, LedgerRecord.need_id.in_(needs)),
        (Engagement, Engagement.id.in_(engagements)),
        (MatchCandidate, MatchCandidate.match_id.in_(runs)),
        (MatchRun, MatchRun.id.in_(runs)),
        (NeedTranslation, NeedTranslation.need_id.in_(needs)),
        (Need, Need.id.in_(needs)),
        (TalentSkill, TalentSkill.talent_id.in_(talents)),
        (TrustItem, TrustItem.talent_id.in_(talents)),
        (Talent, Talent.id.in_(talents)),
        (
            AuditLog,
            AuditLog.office_id.in_(offices)
            | (AuditLog.office_id.is_(None) & AuditLog.actor_user_id.in_(users)),
        ),
        (
            OfficeMember,
            OfficeMember.office_id.in_(offices) & OfficeMember.user_id.in_(users),
        ),
        (MunicipalityMember, MunicipalityMember.user_id.in_(users)),
        (Office, Office.is_demo.is_(True)),
        (User, User.is_demo.is_(True)),
    ]
    for model, predicate in groups:
        for row in db.session.scalars(select(model).where(predicate)):
            db.session.delete(row)
        db.session.flush()
