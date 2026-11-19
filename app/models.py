from datetime import UTC, datetime
from enum import StrEnum
from secrets import token_hex

from flask_login import UserMixin
from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import generate_password_hash

from app.extensions import Base
from tsunagu_rules.types import NeedKind


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(StrEnum):
    OFFICE = "office"
    TSUNAGITE = "tsunagite"
    MUNICIPALITY = "municipality"
    ADMIN = "admin"


class MemberRole(StrEnum):
    OWNER = "owner"
    STAFF = "staff"


class Created:
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class User(UserMixin, Created, Base):  # type: ignore[misc]
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    session_nonce: Mapped[str] = mapped_column(String(32))
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            native_enum=False,
            length=20,
            values_callable=lambda roles: [r.value for r in roles],
            create_constraint=True,
        )
    )
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)
        self.session_nonce = token_hex(16)

    def get_id(self) -> str:
        return f"{self.id}:{self.session_nonce}"


class Municipality(Created, Base):
    __tablename__ = "municipalities"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(5), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    region_group: Mapped[str] = mapped_column(String(50))


class MunicipalityMember(Created, Base):
    __tablename__ = "municipality_members"
    __table_args__ = (UniqueConstraint("user_id", "municipality_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))


class TravelTime(Created, Base):
    __tablename__ = "travel_times"
    __table_args__ = (
        UniqueConstraint("from_municipality_id", "to_municipality_id"),
        CheckConstraint(
            "from_municipality_id <> to_municipality_id", name="distinct_municipalities"
        ),
        CheckConstraint("minutes >= 0", name="nonnegative_minutes"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))
    to_municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))
    minutes: Mapped[int]


class Office(Created, Base):
    __tablename__ = "offices"
    __table_args__ = (
        CheckConstraint(
            "hourly_rate_yen BETWEEN 1000 AND 20000", name="hourly_rate_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))
    hourly_rate_yen: Mapped[int] = mapped_column(default=4000)
    software: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class OfficeMember(Created, Base):
    __tablename__ = "office_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    member_role: Mapped[MemberRole] = mapped_column(
        Enum(
            MemberRole,
            native_enum=False,
            length=20,
            values_callable=lambda roles: [r.value for r in roles],
            create_constraint=True,
        )
    )


class AuditLog(Created, Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[int | None]
    office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id"))
    ip_hash: Mapped[str | None] = mapped_column(String(64))


class NeedStatus(StrEnum):
    DRAFT = "draft"
    BLOCKED = "blocked"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    ENGAGED = "engaged"
    CLOSED = "closed"


class TranslationSource(StrEnum):
    RULE = "rule"
    MANUAL = "manual"
    AI_ACCEPTED = "ai_accepted"


class ItTask(StrEnum):
    REMINDER_FLOW = "reminder_flow"
    INTAKE_FORM = "intake_form"
    COMPARE_SHEET = "compare_sheet"
    IMPORT_RULE = "import_rule"
    TEMPLATE = "template"
    API_LINK = "api_link"
    MANUAL = "manual"
    TRAINING = "training"


def stored_enum[T: StrEnum](kind: type[T]) -> Enum:
    return Enum(
        kind,
        native_enum=False,
        length=20,
        values_callable=lambda items: [item.value for item in items],
        create_constraint=True,
    )


class Need(Created, Base):
    __tablename__ = "needs"
    __table_args__ = (
        CheckConstraint(
            "monthly_hours BETWEEN 1 AND 400", name="need_monthly_hours_range"
        ),
        CheckConstraint(
            "weekdays_mask BETWEEN 1 AND 127", name="need_weekdays_mask_range"
        ),
        Index("ix_needs_status_published_at", "status", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    monthly_hours: Mapped[int]
    weekdays_mask: Mapped[int]
    status: Mapped[NeedStatus] = mapped_column(
        stored_enum(NeedStatus), default=NeedStatus.DRAFT
    )
    block_reason: Mapped[str | None] = mapped_column(String(200))
    value_yen_month: Mapped[int | None]
    rules_version: Mapped[str | None] = mapped_column(String(20))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None]
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class NeedTranslation(Created, Base):
    __tablename__ = "need_translations"
    __table_args__ = (UniqueConstraint("need_id", "category", "it_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("needs.id"))
    category: Mapped[NeedKind] = mapped_column(stored_enum(NeedKind))
    it_task: Mapped[ItTask] = mapped_column(stored_enum(ItTask))
    evidence: Mapped[str] = mapped_column(String(200))
    source: Mapped[TranslationSource] = mapped_column(stored_enum(TranslationSource))
