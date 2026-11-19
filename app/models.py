from datetime import UTC, date, datetime
from enum import StrEnum
from secrets import token_hex

from flask_login import UserMixin
from sqlalchemy import (
    JSON,
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


class BookkeepingLevel(StrEnum):
    NONE = "none"
    THIRD = "3"
    SECOND = "2"
    FIRST = "1"


class TalentAffiliation(StrEnum):
    GRADUATE = "graduate"
    KYORYOKUTAI = "kyoryokutai"
    OTHER = "other"
    NONE = "none"


class TalentSkillKind(StrEnum):
    TOOL = "tool"
    STRENGTH = "strength"


class TrustKind(StrEnum):
    PLEDGE = "pledge"
    TRAINING = "training"


class Talent(Created, Base):
    __tablename__ = "talents"
    __table_args__ = (
        CheckConstraint("capacity BETWEEN 1 AND 5", name="talent_capacity_range"),
        CheckConstraint(
            "weekdays_mask BETWEEN 0 AND 127", name="talent_weekdays_mask_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))
    bookkeeping_level: Mapped[BookkeepingLevel] = mapped_column(
        Enum(
            BookkeepingLevel,
            native_enum=False,
            length=10,
            values_callable=lambda levels: [level.value for level in levels],
            create_constraint=True,
        ),
        default=BookkeepingLevel.NONE,
    )
    level_verified_at: Mapped[datetime | None]
    level_verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    weekdays_mask: Mapped[int] = mapped_column(default=0)
    capacity: Mapped[int] = mapped_column(default=2)
    affiliation: Mapped[TalentAffiliation] = mapped_column(
        stored_enum(TalentAffiliation), default=TalentAffiliation.NONE
    )
    is_listed: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class TalentSkill(Created, Base):
    __tablename__ = "talent_skills"
    __table_args__ = (
        UniqueConstraint("talent_id", "kind", "tag"),
        CheckConstraint(
            "(kind = 'tool' AND tag IN "
            "('freee', 'mf', 'yayoi', 'excel', 'sheets', 'python', 'other')) "
            "OR (kind = 'strength' AND tag IN "
            "('collect', 'check', 'entry', 'report', 'tooling', 'teaching'))",
            name="talent_skill_valid_tag",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    talent_id: Mapped[int] = mapped_column(ForeignKey("talents.id"))
    kind: Mapped[TalentSkillKind] = mapped_column(stored_enum(TalentSkillKind))
    tag: Mapped[str] = mapped_column(String(20))


class TrustItem(Created, Base):
    __tablename__ = "trust_items"
    __table_args__ = (
        CheckConstraint("expires_on >= valid_from", name="trust_period_order"),
        Index("ix_trust_items_talent_kind", "talent_id", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    talent_id: Mapped[int] = mapped_column(ForeignKey("talents.id"))
    kind: Mapped[TrustKind] = mapped_column(stored_enum(TrustKind))
    valid_from: Mapped[date]
    expires_on: Mapped[date]
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class CandidateStatus(StrEnum):
    LISTED = "listed"
    INVITED = "invited"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CLOSED = "closed"
    EXPIRED = "expired"


class EngagementStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    ENDED = "ended"


class MatchRun(Created, Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("needs.id"), index=True)
    rules_version: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    excluded_summary: Mapped[dict[str, int]] = mapped_column(JSON)
    run_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    run_at: Mapped[datetime] = mapped_column(default=utc_now)


class MatchCandidate(Created, Base):
    __tablename__ = "match_candidates"
    __table_args__ = (
        UniqueConstraint("match_id", "talent_id"),
        UniqueConstraint("match_id", "rank"),
        CheckConstraint("`rank` BETWEEN 1 AND 3", name="candidate_rank_range"),
        CheckConstraint("score BETWEEN 0 AND 100", name="candidate_score_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    talent_id: Mapped[int] = mapped_column(ForeignKey("talents.id"))
    rank: Mapped[int]
    score: Mapped[int]
    reasons: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[CandidateStatus] = mapped_column(
        stored_enum(CandidateStatus), default=CandidateStatus.LISTED
    )
    offer_weekday: Mapped[int | None]
    offer_fee_share_bp: Mapped[int | None]
    offer_fee_cap_yen: Mapped[int | None]
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    invited_at: Mapped[datetime | None]
    responded_at: Mapped[datetime | None]


class Engagement(Created, Base):
    __tablename__ = "engagements"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="engagement_weekday_range"),
        CheckConstraint("fee_share_bp BETWEEN 1 AND 4999", name="engagement_fee_range"),
        CheckConstraint("fee_cap_yen >= 0", name="engagement_fee_cap_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"), index=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("needs.id"), unique=True)
    talent_id: Mapped[int] = mapped_column(ForeignKey("talents.id"))
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("match_candidates.id"), unique=True
    )
    weekday: Mapped[int]
    hourly_rate_yen: Mapped[int]
    fee_share_bp: Mapped[int]
    fee_cap_yen: Mapped[int | None]
    trial_ends_on: Mapped[date]
    status: Mapped[EngagementStatus] = mapped_column(
        stored_enum(EngagementStatus), default=EngagementStatus.TRIAL
    )
    agreed_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    end_reason: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class LedgerRecord(Created, Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("need_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("needs.id"))
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    seq: Mapped[int]
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
