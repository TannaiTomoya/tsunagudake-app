from datetime import UTC, datetime
from enum import StrEnum
from secrets import token_hex

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import generate_password_hash

from app.extensions import Base


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
