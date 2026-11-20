import re
from dataclasses import dataclass
from datetime import date

from flask import abort
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import (
    Engagement,
    EngagementStatus,
    Need,
    Reduction,
    ReductionStatus,
    ReturnedKind,
    Statement,
    StatementStatus,
    Talent,
    Visit,
    VisitStatus,
    today_in_japan,
    utc_now,
)
from app.services.ledger import append_record
from app.services.mail import notify_office
from tsunagu_rules import RULES_VERSION
from tsunagu_rules.types import Fee, NeedKind
from tsunagu_rules.types import Reduction as RuleReduction
from tsunagu_rules.value import calc_fee


class StatementConflict(Exception):
    pass


@dataclass(frozen=True)
class VisitInput:
    day: date
    minutes: int
    category: NeedKind
    note: str | None
    learned_term: str | None = None


@dataclass(frozen=True)
class ReductionInput:
    month: str
    category: NeedKind
    before: int
    after: int


def valid_month(month: str) -> bool:
    return (
        bool(re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month)) and month[:4] != "0000"
    )


def confirmed_visits(talent_id: int) -> int:
    return (
        db.session.scalar(
            select(func.count(Visit.id))
            .join(Engagement)
            .where(
                Engagement.talent_id == talent_id, Visit.status == VisitStatus.CONFIRMED
            )
        )
        or 0
    )


def _lock(engagement: Engagement) -> Need:
    # Use the same parent lock as the agreement and ledger writers.
    need = db.session.scalar(
        select(Need)
        .where(
            Need.id == engagement.need_id,
            Need.office_id == engagement.office_id,
        )
        .with_for_update()
    )
    if need is None:
        abort(404)
    return need


def find_visit(engagement: Engagement, visit_id: int, *, lock: bool = False) -> Visit:
    query = select(Visit).where(
        Visit.id == visit_id, Visit.engagement_id == engagement.id
    )
    visit = db.session.scalar(query.with_for_update() if lock else query)
    if visit is None:
        abort(404)
    return visit


def _commit() -> None:
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise StatementConflict(
            "保存できませんでした。入力内容をご確認ください。"
        ) from None


def save_visit(
    engagement: Engagement, data: VisitInput, visit_id: int | None = None
) -> None:
    _lock(engagement)
    state = db.session.scalar(
        select(Engagement.status).where(Engagement.id == engagement.id)
    )
    if state == EngagementStatus.ENDED:
        raise StatementConflict("終わった伴走には、訪問を記録できません。")
    if data.day > today_in_japan() or not 15 <= data.minutes <= 600:
        raise StatementConflict("訪問の日付と時間をご確認ください。")
    visit = (
        find_visit(engagement, visit_id, lock=True) if visit_id is not None else None
    )
    if visit and visit.status != VisitStatus.RECORDED:
        raise StatementConflict(
            "確認済み、または「相違あり」になった訪問は変更できません。"
        )
    if visit is None:
        visit = Visit(engagement_id=engagement.id)
        db.session.add(visit)
    visit.visit_date = data.day
    visit.minutes = data.minutes
    visit.category = data.category
    visit.note = data.note
    visit.learned_term = data.learned_term
    if visit_id is None:
        notify_office(
            engagement.office_id,
            "訪問の確認をお願いします",
            "伴走画面で訪問の記録を確認してください。",
        )
    _commit()


def review_visit(
    engagement: Engagement, visit_id: int, actor: int, *, disputed: bool
) -> None:
    need = _lock(engagement)
    visit = find_visit(engagement, visit_id, lock=True)
    if visit.status != VisitStatus.RECORDED:
        raise StatementConflict("この訪問は確認済みです。")
    visit.status = VisitStatus.DISPUTED if disputed else VisitStatus.CONFIRMED
    visit.confirmed_by = actor
    visit.confirmed_at = utc_now()
    action = "objection" if disputed else "visit_confirmed"
    append_record(
        need,
        action,
        actor,
        {
            "visit_id": visit.id,
            "visit_date": visit.visit_date.isoformat(),
            "category": visit.category.value,
            "minutes": visit.minutes,
        },
    )
    record_audit(
        action,
        actor_id=actor,
        office_id=engagement.office_id,
        target_type="visit",
        target_id=visit.id,
    )
    _commit()


def month_reductions(
    engagement: Engagement, month: str, *, lock: bool = False
) -> list[Reduction]:
    query = (
        select(Reduction)
        .where(
            Reduction.engagement_id == engagement.id,
            Reduction.month == month,
        )
        .order_by(Reduction.category)
    )
    return list(db.session.scalars(query.with_for_update() if lock else query))


def find_statement(
    engagement: Engagement, month: str, *, lock: bool = False
) -> Statement | None:
    query = select(Statement).where(
        Statement.engagement_id == engagement.id, Statement.month == month
    )
    return db.session.scalar(query.with_for_update() if lock else query)


def _check_open(engagement: Engagement, month: str) -> None:
    current = find_statement(engagement, month, lock=True)
    if current and current.status == StatementStatus.FINALIZED:
        raise StatementConflict("確定済みの月は変更できません。")


def save_reduction(engagement: Engagement, data: ReductionInput) -> None:
    _lock(engagement)
    _check_open(engagement, data.month)
    rows = month_reductions(engagement, data.month, lock=True)
    row = next((item for item in rows if item.category == data.category), None)
    if row and row.status == ReductionStatus.CONFIRMED:
        raise StatementConflict("確認済みの時間は変更できません。")
    if row is None:
        notify_office(
            engagement.office_id,
            "減った時間の確認をお願いします",
            "伴走画面で減った時間を確認してください。",
            owners_only=True,
        )
        row = Reduction(
            engagement_id=engagement.id, month=data.month, category=data.category
        )
        db.session.add(row)
    row.before_hours = data.before
    row.after_hours = data.after
    _commit()


def confirm_reduction(engagement: Engagement, reduction_id: int, actor: int) -> str:
    need = _lock(engagement)
    row = db.session.scalar(
        select(Reduction)
        .where(
            Reduction.id == reduction_id,
            Reduction.engagement_id == engagement.id,
        )
        .with_for_update()
    )
    if row is None:
        abort(404)
    _check_open(engagement, row.month)
    if row.status != ReductionStatus.DRAFT:
        raise StatementConflict("この時間は確認済みです。")
    row.status = ReductionStatus.CONFIRMED
    row.confirmed_by = actor
    row.confirmed_at = utc_now()
    append_record(
        need,
        "reduction_confirmed",
        actor,
        {
            "month": row.month,
            "category": row.category.value,
            "before_hours": row.before_hours,
            "after_hours": row.after_hours,
        },
    )
    month = row.month
    _commit()
    return month


def fee_preview(engagement: Engagement, rows: list[Reduction]) -> Fee:
    return calc_fee(
        (
            RuleReduction(
                row.before_hours,
                row.after_hours,
                row.status == ReductionStatus.CONFIRMED,
            )
            for row in rows
        ),
        engagement.hourly_rate_yen,
        engagement.fee_share_bp,
        engagement.fee_cap_yen,
    )


def finalize(
    engagement: Engagement,
    month: str,
    actor: int,
    *,
    returned_to: str = "",
    returned_kind: str = "",
    due_date: str = "",
) -> None:
    need = _lock(engagement)
    statement = find_statement(engagement, month, lock=True)
    if statement and statement.status == StatementStatus.FINALIZED:
        return
    returned_to = returned_to.strip()
    if len(returned_to) > 100:
        raise StatementConflict("時間の戻った先は100文字以内で入力してください。")
    try:
        kind = ReturnedKind(returned_kind) if returned_kind else None
    except ValueError:
        raise StatementConflict("戻った先は、選択肢から選んでください。") from None
    rows = month_reductions(engagement, month, lock=True)
    if not rows:
        raise StatementConflict("確かめる時間の記録を入力してから確定してください。")
    if any(row.status != ReductionStatus.CONFIRMED for row in rows):
        raise StatementConflict("確かめていない時間があるため確定できません。")
    fee = fee_preview(engagement, rows)
    if statement is None:
        statement = Statement(engagement_id=engagement.id, month=month)
        db.session.add(statement)
    statement.reduced_hours = fee.reduced_hours
    statement.value_yen = fee.value_yen
    statement.fee_yen = fee.fee_yen
    statement.waived = fee.waived
    statement.hourly_rate_yen = engagement.hourly_rate_yen
    statement.fee_share_bp = engagement.fee_share_bp
    statement.fee_cap_yen = engagement.fee_cap_yen
    statement.rules_version = RULES_VERSION
    statement.status = StatementStatus.FINALIZED
    try:
        statement.due_on = date.fromisoformat(due_date) if due_date else None
    except ValueError:
        raise StatementConflict("振込期日の日付をご確認ください。") from None
    talent = db.session.get(Talent, engagement.talent_id)
    if talent:
        statement.payout_cipher = talent.payout_cipher
        statement.invoice_registration = talent.invoice_registration
    statement.returned_to = returned_to or None
    statement.returned_kind = kind
    statement.finalized_by = actor
    statement.finalized_at = utc_now()
    append_record(
        need,
        "statement_finalized",
        actor,
        {
            "month": month,
            "reduced_hours": fee.reduced_hours,
            "value_yen": fee.value_yen,
            "fee_yen": fee.fee_yen,
            "waived": fee.waived,
            "due_on": due_date or None,
        },
    )
    record_audit(
        "statement_finalized",
        actor_id=actor,
        office_id=engagement.office_id,
        target_type="engagement",
        target_id=engagement.id,
    )
    _commit()


def record_payment(
    engagement: Engagement, month: str, actor: int, *, receipt: bool
) -> None:
    need = _lock(engagement)
    statement = find_statement(engagement, month, lock=True)
    if (
        not statement
        or statement.status != StatementStatus.FINALIZED
        or not statement.fee_yen
    ):
        raise StatementConflict("支払金額のある確定明細を選んでください。")
    if receipt:
        if statement.receipt_confirmed_at:
            return
        if not statement.transfer_reported_at:
            raise StatementConflict("事務所の振込報告を待ってください。")
        statement.receipt_confirmed_at = utc_now()
    else:
        if statement.transfer_reported_at:
            return
        statement.transfer_reported_at = utc_now()
    append_record(
        need,
        "receipt_confirmed" if receipt else "transfer_reported",
        actor,
        {"month": month, "fee_yen": statement.fee_yen},
    )
    _commit()


def expected_income(talent_id: int, month: str) -> int:
    return int(
        db.session.scalar(
            select(func.sum(Statement.fee_yen))
            .join(Engagement)
            .where(
                Engagement.talent_id == talent_id,
                Statement.month == month,
                Statement.status == StatementStatus.FINALIZED,
                Statement.receipt_confirmed_at.is_(None),
            )
        )
        or 0
    )
