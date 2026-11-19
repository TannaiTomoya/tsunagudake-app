import json
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select

from app.extensions import db
from app.models import LedgerRecord, Need, utc_now
from tsunagu_rules import RULES_VERSION, ledger
from tsunagu_rules.types import JsonValue, LedgerEntry, StoredEntry, VerifyResult


def records_for_need(need: Need) -> list[LedgerRecord]:
    return list(
        db.session.scalars(
            select(LedgerRecord)
            .where(
                LedgerRecord.need_id == need.id,
                LedgerRecord.office_id == need.office_id,
            )
            .order_by(LedgerRecord.seq)
        )
    )


def append_record(
    need: Need,
    kind: str,
    actor_id: int,
    data: dict[str, JsonValue],
    *,
    recorded_at: datetime | None = None,
) -> None:
    """Append while the caller holds the need lock, within the same transaction."""
    previous = db.session.scalar(
        select(LedgerRecord)
        .where(LedgerRecord.need_id == need.id)
        .order_by(LedgerRecord.seq.desc())
        .limit(1)
        .with_for_update()
        .execution_options(autoflush=False)
    )
    prev_hash = previous.entry_hash if previous else ledger.GENESIS_HASH
    # MySQL rounds fractional seconds for DATETIME without fractional precision.
    at = (recorded_at or utc_now()).replace(microsecond=0)
    entry = LedgerEntry(
        previous.seq + 1 if previous else 1,
        actor_id,
        need.id,
        at.replace(tzinfo=UTC),
        kind,
        need.rules_version or RULES_VERSION,
        data,
    )
    db.session.add(
        LedgerRecord(
            need_id=need.id,
            office_id=need.office_id,
            seq=entry.seq,
            kind=kind,
            payload=ledger.canonical_payload(entry),
            prev_hash=prev_hash,
            entry_hash=ledger.append(prev_hash, entry),
            actor_user_id=actor_id,
            created_at=at,
        )
    )


def verify_records(rows: Iterable[LedgerRecord]) -> VerifyResult:
    entries = []
    for row in rows:
        try:
            data = json.loads(row.payload)
            entry = LedgerEntry(
                row.seq,
                row.actor_user_id,
                row.need_id,
                row.created_at.replace(tzinfo=UTC),
                row.kind,
                data["rules"],
                data["data"],
            )
            if ledger.canonical_payload(entry) != row.payload:
                return VerifyResult(False, row.seq)
            entries.append(StoredEntry(entry, row.prev_hash, row.entry_hash))
        except KeyError, TypeError, ValueError, AttributeError:
            return VerifyResult(False, row.seq)
    return ledger.verify(entries)
