from dataclasses import dataclass

from flask import url_for
from sqlalchemy import select

from app.extensions import db
from app.models import (
    CandidateStatus,
    Engagement,
    MatchCandidate,
    MatchRun,
    MemberRole,
    Need,
    NeedStatus,
    Office,
    OfficeMember,
    Reduction,
    ReductionStatus,
    Role,
    Statement,
    StatementStatus,
    Talent,
    TrustKind,
    User,
    Visit,
    VisitStatus,
    today_in_japan,
)
from app.services.trust import trust_statuses


@dataclass(frozen=True)
class Task:
    title: str
    reason: str
    link: str


def tasks_for(actor: User) -> list[Task]:
    tasks: list[Task] = []
    if actor.role == Role.OFFICE:
        membership = db.session.scalar(
            select(OfficeMember)
            .join(Office)
            .where(OfficeMember.user_id == actor.id, Office.is_active.is_(True))
        )
        if membership is None:
            return tasks
        office = membership.office_id
        base = select(Engagement.id).where(Engagement.office_id == office)
        for eid in db.session.scalars(
            base.join(Visit)
            .where(Visit.status == VisitStatus.RECORDED)
            .distinct()
            .order_by(Engagement.id)
        ):
            tasks.append(
                Task(
                    "訪問を確認する",
                    f"伴走 {eid} に確認待ちの訪問があります。",
                    url_for(
                        "matching.office_engagement",
                        office_id=office,
                        engagement_id=eid,
                        _anchor="visits",
                    ),
                )
            )
        if membership.member_role == MemberRole.OWNER:
            for eid, month in db.session.execute(
                base.add_columns(Reduction.month)
                .join(Reduction)
                .where(Reduction.status == ReductionStatus.DRAFT)
                .distinct()
                .order_by(Reduction.month, Engagement.id)
            ):
                tasks.append(
                    Task(
                        "減った時間を確認する",
                        f"伴走 {eid} の {month} に下書きがあります。",
                        url_for(
                            "matching.office_engagement",
                            office_id=office,
                            engagement_id=eid,
                            month=month,
                            _anchor="statement",
                        ),
                    )
                )
            for eid, month in db.session.execute(
                base.add_columns(Statement.month)
                .join(Statement)
                .where(
                    Statement.status == StatementStatus.FINALIZED,
                    Statement.fee_yen > 0,
                    Statement.transfer_reported_at.is_(None),
                )
                .order_by(Statement.month, Engagement.id)
            ):
                tasks.append(
                    Task(
                        "振込を報告する",
                        f"伴走 {eid} の {month} の明細が確定しています。",
                        url_for(
                            "matching.office_engagement",
                            office_id=office,
                            engagement_id=eid,
                            month=month,
                            _anchor="statement",
                        ),
                    )
                )
        pending = (
            select(Need.id)
            .join(MatchRun)
            .join(MatchCandidate)
            .where(
                Need.office_id == office,
                Need.status == NeedStatus.PUBLISHED,
                MatchCandidate.status == CandidateStatus.INVITED,
            )
        )
        for nid in db.session.scalars(pending.distinct().order_by(Need.id)):
            tasks.append(
                Task(
                    "声かけの返事を待つ",
                    f"困りごと {nid} は回答待ちです。",
                    url_for("matching.candidates", office_id=office, need_id=nid),
                )
            )
    elif actor.role == Role.TSUNAGITE:
        talent = db.session.scalar(select(Talent).where(Talent.user_id == actor.id))
        if talent is None:
            return tasks
        home = url_for("auth.talent_home")
        invited = db.session.scalar(
            select(MatchCandidate.id)
            .join(MatchRun)
            .join(Need)
            .join(Office)
            .where(
                MatchCandidate.talent_id == talent.id,
                MatchCandidate.status == CandidateStatus.INVITED,
                Need.status == NeedStatus.PUBLISHED,
                Office.is_active.is_(True),
            )
            .limit(1)
        )
        if invited:
            tasks.append(
                Task("声かけに返事をする", "事務所から声かけが届いています。", home)
            )
        for eid, month in db.session.execute(
            select(Engagement.id, Statement.month)
            .join(Statement)
            .where(
                Engagement.talent_id == talent.id,
                Statement.status == StatementStatus.FINALIZED,
                Statement.transfer_reported_at.is_not(None),
                Statement.receipt_confirmed_at.is_(None),
            )
            .order_by(Statement.month, Engagement.id)
        ):
            tasks.append(
                Task(
                    "入金を確認する",
                    f"伴走 {eid} の {month} に振込報告があります。",
                    url_for(
                        "matching.talent_engagement",
                        engagement_id=eid,
                        month=month,
                        _anchor="statement",
                    ),
                )
            )
        for status in trust_statuses(talent, today_in_japan()):
            if status.days_left is None or status.days_left <= 30:
                label = "守秘の誓約" if status.kind == TrustKind.PLEDGE else "研修"
                reason = (
                    f"有効期限まであと{status.days_left}日です。"
                    if status.valid
                    else "現在有効な記録がありません。"
                )
                tasks.append(
                    Task(
                        label + "を確認する",
                        reason,
                        url_for("talent.pledge")
                        if status.kind == TrustKind.PLEDGE
                        else home,
                    )
                )
    return tasks
