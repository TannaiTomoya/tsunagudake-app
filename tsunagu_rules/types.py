from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class NeedKind(StrEnum):
    COLLECT = "collect"
    CHECK = "check"
    ENTRY = "entry"
    REPORT = "report"
    TOOLING = "tooling"
    TEACHING = "teaching"


class Exclusion(StrEnum):
    PLEDGE = "pledge"
    TRAINING = "training"
    TRANSLATION = "translation"
    DISTANCE = "distance"
    WEEKDAY = "weekday"
    CAPACITY = "capacity"
    UNLISTED = "unlisted"
    DECLINED = "declined"


@dataclass(frozen=True)
class Pattern:
    kind: NeedKind
    words: tuple[str, ...]
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class Dictionary:
    version: str
    taxwork: tuple[str, ...]
    patterns: tuple[Pattern, ...]


@dataclass(frozen=True)
class Translation:
    input_hash: str
    blocked: bool
    matched: bool
    blocked_words: tuple[str, ...] = ()
    patterns: tuple[Pattern, ...] = ()


@dataclass(frozen=True)
class ValueEstimate:
    monthly_value_yen: int
    projected_hours: tuple[int, int, int]
    projected_value_yen: tuple[int, int, int]


@dataclass(frozen=True)
class Need:
    municipality: str
    weekdays: frozenset[int]
    kinds: frozenset[NeedKind]
    software: str
    declined_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Talent:
    id: int
    municipality: str
    weekdays: frozenset[int]
    skills: frozenset[NeedKind]
    software: frozenset[str]
    pledge_until: date | None
    training_until: date | None
    active_count: int
    capacity: int
    is_listed: bool
    suspended: bool
    verified_bookkeeping_grade: int | None
    verified_visits: int
    translation_verified: bool = False


@dataclass(frozen=True)
class MatchLimits:
    travel_minutes: int = 60


@dataclass(frozen=True)
class Candidate:
    talent_id: int
    score: int
    travel_minutes: int
    verified_visits: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MatchResult:
    candidates: tuple[Candidate, ...]
    excluded: Mapping[Exclusion, int]


@dataclass(frozen=True)
class Reduction:
    before_hours: int
    after_hours: int
    confirmed: bool


@dataclass(frozen=True)
class Fee:
    reduced_hours: int
    value_yen: int
    fee_yen: int
    waived: bool


type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    actor: int
    need: int
    at: datetime
    kind: str
    rules: str
    data: Mapping[str, JsonValue]


@dataclass(frozen=True)
class StoredEntry:
    entry: LedgerEntry
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True)
class VerifyResult:
    valid: bool
    invalid_seq: int | None = None


@dataclass(frozen=True)
class RegionRow:
    municipality: str
    offices: int
    talents: int
    corps_talents: int
    visits: int
    reduced_hours: int


@dataclass(frozen=True)
class RegionMetrics:
    offices: int | None
    talents: int | None
    corps_talents: int | None
    visits: int | None
    reduced_hours: int | None


@dataclass(frozen=True)
class MaskedRegionRow:
    municipality: str
    metrics: RegionMetrics


@dataclass(frozen=True)
class RegionView:
    rows: tuple[MaskedRegionRow, ...]
    total: RegionMetrics | None
