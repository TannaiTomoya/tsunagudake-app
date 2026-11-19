from dataclasses import dataclass

from sqlalchemy import case, func, select

from app.extensions import db
from app.models import (
    Engagement,
    Municipality,
    Office,
    Reduction,
    ReductionStatus,
    Role,
    Talent,
    TalentAffiliation,
    User,
    Visit,
    VisitStatus,
)
from tsunagu_rules.region import mask_region
from tsunagu_rules.types import RegionMetrics, RegionRow


@dataclass(frozen=True)
class Dashboard:
    town: RegionMetrics
    total: RegionMetrics | None


def dashboard_for(
    municipality: Municipality, threshold: int, *, demo: bool = False
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
    hours = (
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
        .subquery()
    )
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
    return Dashboard(town, view.total)
