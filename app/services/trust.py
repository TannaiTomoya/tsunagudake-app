from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.models import Talent, TrustItem, TrustKind


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
