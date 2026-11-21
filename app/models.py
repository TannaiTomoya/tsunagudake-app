from datetime import UTC, date, datetime
from enum import StrEnum
from secrets import token_hex
from zoneinfo import ZoneInfo

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


def today_in_japan() -> date:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


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


class SecondFactor(Base):
    __tablename__ = "second_factors"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_cipher: Mapped[str] = mapped_column(String(255))
    enrollment_ticket: Mapped[str | None] = mapped_column(String(48))
    last_step: Mapped[int | None]
    recovery_hashes: Mapped[list[str]] = mapped_column(JSON, default=list)


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
        CheckConstraint(
            "client_count BETWEEN 0 AND 100000", name="office_client_count_range"
        ),
        CheckConstraint(
            "lat_e6 BETWEEN -90000000 AND 90000000", name="office_lat_range"
        ),
        CheckConstraint(
            "lon_e6 BETWEEN -180000000 AND 180000000", name="office_lon_range"
        ),
        UniqueConstraint(
            "collection_token_hash", name="uq_offices_collection_token_hash"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))
    hourly_rate_yen: Mapped[int] = mapped_column(default=4000)
    software: Mapped[str | None] = mapped_column(String(20))
    client_count: Mapped[int | None]
    # 住所は、合意したあとの伴走の画面で、担当つなぎ手にだけ見せる。
    address: Mapped[str | None] = mapped_column(String(200))
    # 事務所の位置（100万分の1度）。到着の確認にだけ使い、画面には出さない。
    lat_e6: Mapped[int | None]
    lon_e6: Mapped[int | None]
    # 撮って送るだけ帳簿から件数を受け取るための合言葉。ハッシュだけを持つ。
    collection_token_hash: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class CollectionItem(Created, Base):
    """資料回収ボードの1マス。顧問先は番号だけで持つ（名前・金額を持たない）。"""

    __tablename__ = "collection_items"
    __table_args__ = (
        UniqueConstraint("office_id", "month", "client_no"),
        CheckConstraint(
            "client_no BETWEEN 1 AND 9999", name="collection_client_no_range"
        ),
        CheckConstraint(
            "received_count BETWEEN 0 AND 9999", name="collection_received_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    month: Mapped[str] = mapped_column(String(7))
    client_no: Mapped[int]
    is_ready: Mapped[bool] = mapped_column(default=False)
    received_count: Mapped[int] = mapped_column(default=0)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class OfficeRoute(Created, Base):
    """通常の事務所が、自分で確かめた経路。追記と、分の修正だけ。"""

    __tablename__ = "office_routes"
    __table_args__ = (
        UniqueConstraint("office_id", "from_municipality_id"),
        CheckConstraint("minutes BETWEEN 1 AND 300", name="office_route_minutes_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    from_municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"))
    minutes: Mapped[int]
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(default=utc_now)


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


def stored_enum[T: StrEnum](kind: type[T], *, name: str | None = None) -> Enum:
    return Enum(
        kind,
        name=name or kind.__name__.lower(),
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
    payout_cipher: Mapped[str | None] = mapped_column(Text)
    invoice_registration: Mapped[str | None] = mapped_column(String(14))
    translation_verified_at: Mapped[datetime | None]
    translation_verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
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
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_office_id: Mapped[int | None] = mapped_column(
        ForeignKey("offices.id", ondelete="SET NULL")
    )


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
    pledge_version: Mapped[str | None] = mapped_column(String(20))
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
    estimate_hours: Mapped[int | None]
    estimate_hourly_rate: Mapped[int | None]
    estimate_total: Mapped[int | None]
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
    # 訪問の最後に決めた、次の訪問日。
    next_visit_on: Mapped[date | None]
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


class Checkin(Created, Base):
    """訪問の日の「向かいます」「着きました」。緯度・経度を持つ列は無い。"""

    __tablename__ = "checkins"
    __table_args__ = (
        UniqueConstraint("engagement_id", "day", name="uq_checkins_engagement_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"))
    day: Mapped[date]
    heading_at: Mapped[datetime | None]
    eta_at: Mapped[datetime | None]
    arrived_at: Mapped[datetime | None]
    # 事務所の近くで押した。None は、位置を確かめていない。
    near: Mapped[bool | None]
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Message(Created, Base):
    """伴走ごとのメッセージ。事務所（代表・職員）と担当つなぎ手の間だけ。"""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_engagement_id_id", "engagement_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"))
    sender_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    from_office: Mapped[bool]
    template: Mapped[str | None] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(String(400))
    read_at: Mapped[datetime | None]


class AiPurpose(StrEnum):
    TRANSLATE = "translate"
    DRAFT = "draft"
    QUESTION = "question"
    SUMMARY = "summary"


class AiOutcome(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


class AiRun(Created, Base):
    """外の AI を呼んだ記録。本文も、返ってきた文も持たない。足すだけ。"""

    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint(
            "chars >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ai_run_counts",
        ),
        Index("ix_ai_runs_created_at", "created_at"),
        Index("ix_ai_runs_need_id_id", "need_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_ai_runs_actor_user_id")
    )
    office_id: Mapped[int | None] = mapped_column(
        ForeignKey("offices.id", name="fk_ai_runs_office_id")
    )
    purpose: Mapped[AiPurpose] = mapped_column(stored_enum(AiPurpose))
    driver: Mapped[str] = mapped_column(String(10))
    chars: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    outcome: Mapped[AiOutcome] = mapped_column(stored_enum(AiOutcome))
    result: Mapped[str | None] = mapped_column(String(100))
    need_id: Mapped[int | None] = mapped_column(
        ForeignKey("needs.id", name="fk_ai_runs_need_id")
    )
    input_hash: Mapped[str | None] = mapped_column(String(64))


class CheckSummary(Created, Base):
    """過去比較の件数だけ。科目・金額・顧問先を持つ列は無い。足すだけ。"""

    __tablename__ = "check_summaries"
    __table_args__ = (
        CheckConstraint(
            "compared BETWEEN 0 AND 9999 AND flagged BETWEEN 0 AND compared "
            "AND uncomparable BETWEEN 0 AND 9999 AND compared + uncomparable >= 1",
            name="check_summary_counts",
        ),
        Index("ix_check_summaries_engagement_month", "engagement_id", "month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"))
    month: Mapped[str] = mapped_column(String(7))
    compared: Mapped[int]
    flagged: Mapped[int]
    uncomparable: Mapped[int]
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class LedgerAnchor(Created, Base):
    """毎日の、鎖の最後のハッシュの控え（同じ中身を DB の外のファイルにも置く）。"""

    __tablename__ = "ledger_anchors"
    __table_args__ = (
        CheckConstraint(
            "chains >= 0 AND entries >= chains AND problems >= 0",
            name="ledger_anchor_counts",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    anchored_on: Mapped[date] = mapped_column(unique=True)
    chains: Mapped[int]
    entries: Mapped[int]
    digest: Mapped[str] = mapped_column(String(64))
    problems: Mapped[int]


class VisitStatus(StrEnum):
    RECORDED = "recorded"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"


class ReductionStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class StatementStatus(StrEnum):
    DRAFT = "draft"
    FINALIZED = "finalized"


class ReturnedKind(StrEnum):
    CLIENT_CONSULT = "client_consult"
    NEW_CLIENT = "new_client"
    LEFT_EARLY = "left_early"
    OTHER = "other"


class Visit(Created, Base):
    __tablename__ = "visits"
    __table_args__ = (
        UniqueConstraint("engagement_id", "visit_date"),
        CheckConstraint("minutes BETWEEN 15 AND 600", name="visit_minutes_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"))
    visit_date: Mapped[date]
    minutes: Mapped[int]
    category: Mapped[NeedKind] = mapped_column(
        stored_enum(NeedKind, name="visit_category")
    )
    learned_term: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(300))
    idea: Mapped[str | None] = mapped_column(String(100))
    idea_adopted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_visits_idea_adopted_by_users")
    )
    idea_adopted_at: Mapped[datetime | None]
    status: Mapped[VisitStatus] = mapped_column(
        stored_enum(VisitStatus), default=VisitStatus.RECORDED
    )
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class Reduction(Created, Base):
    __tablename__ = "reductions"
    __table_args__ = (
        UniqueConstraint("engagement_id", "month", "category"),
        CheckConstraint(
            "before_hours BETWEEN 0 AND 400", name="reduction_before_range"
        ),
        CheckConstraint("after_hours BETWEEN 0 AND 400", name="reduction_after_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"))
    month: Mapped[str] = mapped_column(String(7))
    category: Mapped[NeedKind] = mapped_column(
        stored_enum(NeedKind, name="reduction_category")
    )
    before_hours: Mapped[int]
    after_hours: Mapped[int]
    status: Mapped[ReductionStatus] = mapped_column(
        stored_enum(ReductionStatus), default=ReductionStatus.DRAFT
    )
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class Statement(Created, Base):
    __tablename__ = "statements"
    __table_args__ = (
        UniqueConstraint("engagement_id", "month"),
        CheckConstraint(
            "fee_yen >= 0 AND fee_yen * 2 <= value_yen", name="statement_fee_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"))
    month: Mapped[str] = mapped_column(String(7))
    reduced_hours: Mapped[int]
    hourly_rate_yen: Mapped[int]
    fee_share_bp: Mapped[int]
    fee_cap_yen: Mapped[int | None]
    value_yen: Mapped[int]
    fee_yen: Mapped[int]
    waived: Mapped[bool]
    rules_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[StatementStatus] = mapped_column(stored_enum(StatementStatus))
    finalized_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    finalized_at: Mapped[datetime | None]
    returned_to: Mapped[str | None] = mapped_column(String(100))
    returned_kind: Mapped[ReturnedKind | None] = mapped_column(
        stored_enum(ReturnedKind)
    )
    due_on: Mapped[date | None]
    payout_cipher: Mapped[str | None] = mapped_column(Text)
    invoice_registration: Mapped[str | None] = mapped_column(String(14))
    transfer_reported_at: Mapped[datetime | None]
    receipt_confirmed_at: Mapped[datetime | None]


class RateLimitEvent(Created, Base):
    __tablename__ = "rate_limit_events"
    __table_args__ = (
        Index("ix_rate_limit_events_lookup", "scope", "key_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class RateLimitLock(Base):
    __tablename__ = "rate_limit_locks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)


class Invitation(Created, Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(stored_enum(Role, name="invitation_role"))
    office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id"))
    municipality_id: Mapped[int | None] = mapped_column(ForeignKey("municipalities.id"))
    member_role: Mapped[MemberRole | None] = mapped_column(
        stored_enum(MemberRole, name="invitation_member_role")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class PasswordResetToken(Created, Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    session_nonce: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class OutboxMail(Created, Base):
    __tablename__ = "outbox_mails"
    __table_args__ = (Index("ix_outbox_mails_pending", "sent_at", "is_demo", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient: Mapped[str] = mapped_column(String(255))
    digest_key: Mapped[str | None] = mapped_column(String(48), unique=True)
    deliver_after: Mapped[datetime | None]
    subject: Mapped[str] = mapped_column(String(150))
    body_encrypted: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool]
    expires_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
