from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, select

from app.extensions import db
from app.models import (
    Engagement,
    Municipality,
    Office,
    Reduction,
    ReductionStatus,
    ReturnedKind,
    Role,
    Statement,
    StatementStatus,
    Talent,
    TalentAffiliation,
    User,
    Visit,
    VisitStatus,
)
from tsunagu_rules.region import (
    mask_breakdown,
    mask_cells,
    mask_region,
    recent_months,
)
from tsunagu_rules.types import (
    MaskedCells,
    NeedKind,
    RegionCell,
    RegionMetrics,
    RegionRow,
)

CHART_MONTHS = 6
# 戻った先の並び。最後の None は、選ばずに確定した明細。
RETURNED_ORDER: tuple[ReturnedKind | None, ...] = (*ReturnedKind, None)


@dataclass(frozen=True)
class Charts:
    months: tuple[str, ...]
    hours: MaskedCells
    value_yen: tuple[int | None, ...]
    value_total: int | None
    returned: MaskedCells
    # 協力隊・修了生・そのほか。1つでも伏せるなら None。
    affiliations: tuple[int, ...] | None


@dataclass(frozen=True)
class Dashboard:
    town: RegionMetrics
    total: RegionMetrics | None
    # 町そのものを伏せるときは None。
    charts: Charts | None
    # 顧問先の数を入力した事務所の数と、その合計。伏せるときは None。
    client_offices: int | None
    clients: int | None


def _charts(
    municipality: Municipality, threshold: int, today: date, demo: bool
) -> Charts:
    months = recent_months(today, CHART_MONTHS)
    in_town = (
        Office.municipality_id == municipality.id,
        Office.is_active.is_(True),
        Office.is_demo.is_(demo),
    )
    saved = Reduction.before_hours - Reduction.after_hours
    gained = case((saved > 0, saved), else_=0)
    monthly_query = (
        select(
            Reduction.month,
            func.count(func.distinct(case((saved > 0, Office.id)))),
            func.sum(gained),
            func.sum(gained * Engagement.hourly_rate_yen),
        )
        .join(Engagement, Engagement.id == Reduction.engagement_id)
        .join(Office, Office.id == Engagement.office_id)
        .where(
            *in_town,
            Reduction.status == ReductionStatus.CONFIRMED,
            Reduction.month.in_(months),
        )
        .group_by(Reduction.month)
    )
    if demo:
        monthly_query = monthly_query.where(Reduction.category == NeedKind.COLLECT)
    monthly = {
        month: (int(offices), int(hours), int(yen))
        for month, offices, hours, yen in db.session.execute(monthly_query)
    }
    hours = mask_cells(
        [RegionCell(*monthly.get(month, (0, 0, 0))[:2]) for month in months],
        threshold,
    )
    yen = [monthly.get(month, (0, 0, 0))[2] for month in months]
    # The free-text note (returned_to) is never selected here.
    by_kind = {
        kind: RegionCell(int(offices), int(total))
        for kind, offices, total in db.session.execute(
            select(
                Statement.returned_kind,
                func.count(
                    func.distinct(case((Statement.reduced_hours > 0, Office.id)))
                ),
                func.sum(Statement.reduced_hours),
            )
            .join(Engagement, Engagement.id == Statement.engagement_id)
            .join(Office, Office.id == Engagement.office_id)
            .where(
                *in_town,
                Statement.status == StatementStatus.FINALIZED,
                Statement.month.in_(months),
            )
            .group_by(Statement.returned_kind)
        )
    }
    people = {
        affiliation: int(count)
        for affiliation, count in db.session.execute(
            select(Talent.affiliation, func.count())
            .join(User, User.id == Talent.user_id)
            .where(
                Talent.municipality_id == municipality.id,
                User.is_active.is_(True),
                User.is_demo.is_(demo),
                User.role == Role.TSUNAGITE,
            )
            .group_by(Talent.affiliation)
        )
    }
    corps = people.pop(TalentAffiliation.KYORYOKUTAI, 0)
    graduates = people.pop(TalentAffiliation.GRADUATE, 0)
    return Charts(
        months,
        hours,
        tuple(
            None if cell is None else amount
            for cell, amount in zip(hours.values, yen, strict=True)
        ),
        None if hours.total is None else sum(yen),
        mask_cells(
            [by_kind.get(kind, RegionCell(0, 0)) for kind in RETURNED_ORDER],
            threshold,
        ),
        mask_breakdown([corps, graduates, sum(people.values())], threshold),
    )


def dashboard_for(
    municipality: Municipality, threshold: int, today: date, *, demo: bool = False
) -> Dashboard:
    # Aggregate each fact table before joining, so multiple visits and reductions
    # on the same engagement cannot multiply one another.
    offices = (
        select(Office.municipality_id, func.count().label("count"))
        .where(Office.is_active.is_(True), Office.is_demo.is_(demo))
        .group_by(Office.municipality_id)
        .subquery()
    )
    talents = (
        select(
            Talent.municipality_id,
            func.count().label("count"),
            func.sum(
                case((Talent.affiliation == TalentAffiliation.KYORYOKUTAI, 1), else_=0)
            ).label("corps"),
        )
        .join(User, User.id == Talent.user_id)
        .where(
            User.is_active.is_(True),
            User.is_demo.is_(demo),
            User.role == Role.TSUNAGITE,
        )
        .group_by(Talent.municipality_id)
        .subquery()
    )
    visits = (
        select(Office.municipality_id, func.count().label("count"))
        .join(Engagement, Engagement.office_id == Office.id)
        .join(Visit, Visit.engagement_id == Engagement.id)
        .where(
            Office.is_active.is_(True),
            Office.is_demo.is_(demo),
            Visit.status == VisitStatus.CONFIRMED,
        )
        .group_by(Office.municipality_id)
        .subquery()
    )
    saved_hours = Reduction.before_hours - Reduction.after_hours
    hours_query = (
        select(
            Office.municipality_id,
            func.sum(case((saved_hours > 0, saved_hours), else_=0)).label("total"),
        )
        .join(Engagement, Engagement.office_id == Office.id)
        .join(Reduction, Reduction.engagement_id == Engagement.id)
        .where(
            Office.is_active.is_(True),
            Office.is_demo.is_(demo),
            Reduction.status == ReductionStatus.CONFIRMED,
        )
        .group_by(Office.municipality_id)
    )
    if demo:
        hours_query = hours_query.where(
            Reduction.category == NeedKind.COLLECT,
            Reduction.month == today.strftime("%Y-%m"),
        )
    hours = hours_query.subquery()
    query = (
        select(
            Municipality.code,
            func.coalesce(offices.c.count, 0),
            func.coalesce(talents.c.count, 0),
            func.coalesce(talents.c.corps, 0),
            func.coalesce(visits.c.count, 0),
            func.coalesce(hours.c.total, 0),
        )
        .outerjoin(offices, offices.c.municipality_id == Municipality.id)
        .outerjoin(talents, talents.c.municipality_id == Municipality.id)
        .outerjoin(visits, visits.c.municipality_id == Municipality.id)
        .outerjoin(hours, hours.c.municipality_id == Municipality.id)
        .where(Municipality.region_group == municipality.region_group)
        .order_by(Municipality.code)
    )
    grouped_metrics = db.session.execute(query)
    view = mask_region(
        (
            RegionRow(
                code,
                int(office_n),
                int(talent_n),
                int(corps),
                int(visit_n),
                int(reduced),
            )
            for code, office_n, talent_n, corps, visit_n, reduced in grouped_metrics
        ),
        threshold,
    )
    town = next(
        row.metrics for row in view.rows if row.municipality == municipality.code
    )
    if town.offices is None:
        return Dashboard(town, view.total, None, None, None)
    entered, clients = db.session.execute(
        select(func.count(), func.coalesce(func.sum(Office.client_count), 0)).where(
            Office.municipality_id == municipality.id,
            Office.is_active.is_(True),
            Office.is_demo.is_(demo),
            Office.client_count.is_not(None),
        )
    ).one()
    shown = mask_cells([RegionCell(int(entered), int(clients or 0))], threshold).values[
        0
    ]
    return Dashboard(
        town,
        view.total,
        _charts(municipality, threshold, today, demo),
        None if shown is None else int(entered),
        shown,
    )
