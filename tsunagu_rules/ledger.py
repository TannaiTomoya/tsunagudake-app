import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC

from .types import LedgerEntry, StoredEntry, VerifyResult

GENESIS_HASH = "0" * 64
REQUIRED_BOUNDARIES = (
    "税理士業務（税務代理・税務書類の作成・税務相談）",
    "顧問先の生データの持ち出し",
)


def with_required_boundaries(entry: LedgerEntry) -> LedgerEntry:
    if entry.kind != "agreed":
        return entry
    extra = entry.data.get("not_do", [])
    if not isinstance(extra, list) or not all(isinstance(item, str) for item in extra):
        raise ValueError("「しないこと」は文字列の一覧で指定してください。")
    boundaries = list(dict.fromkeys((*REQUIRED_BOUNDARIES, *extra)))
    return replace(entry, data={**entry.data, "not_do": boundaries})


def canonical_payload(entry: LedgerEntry) -> str:
    entry = with_required_boundaries(entry)
    if entry.at.utcoffset() is None:
        raise ValueError("日時にはタイムゾーンが必要です。")
    return json.dumps(
        {
            "actor": entry.actor,
            "at": entry.at.astimezone(UTC).isoformat(timespec="seconds"),
            "data": dict(entry.data),
            "kind": entry.kind,
            "need": entry.need,
            "rules": entry.rules,
            "seq": entry.seq,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def append(prev_hash: str, entry: LedgerEntry) -> str:
    payload = canonical_payload(entry)
    return hashlib.sha256(f"{prev_hash}\n{payload}".encode()).hexdigest()


def verify(entries: Iterable[StoredEntry]) -> VerifyResult:
    previous = GENESIS_HASH
    for seq, stored in enumerate(entries, start=1):
        entry = stored.entry
        try:
            valid = (
                entry.seq == seq
                and stored.prev_hash == previous
                and entry == with_required_boundaries(entry)
                and stored.entry_hash == append(previous, entry)
            )
        except TypeError, ValueError:
            valid = False
        if not valid:
            return VerifyResult(False, entry.seq)
        previous = stored.entry_hash
    return VerifyResult(True)
