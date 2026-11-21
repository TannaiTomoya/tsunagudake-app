import hmac
import re
from datetime import date
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.extensions import db
from app.models import CollectionItem, Office

MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])\Z")
NUMBER_MAX = 9999
BATCH_MAX = 500
TOKEN_PREFIX = "tsg_"
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
NUMBER_ERROR = "顧問先の番号は、1〜9999の整数で入力してください。名前は入れません。"
MONTH_ERROR = "月を確認できませんでした。ボードを開き直してください。"
SAVE_ERROR = "保存できませんでした。もう一度お試しください。"


class CollectionError(ValueError):
    pass


class CollectionNotFound(LookupError):
    pass


def shift_month(month: str, by: int) -> str:
    year, number = (int(part) for part in month.split("-"))
    index = year * 12 + number - 1 + by
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def parse_month(raw: str | None, today: date) -> str:
    """月を決める。無ければ今月。来月より先と、2020年より前は受け取らない。"""
    current = today.strftime("%Y-%m")
    if not raw:
        return current
    if not MONTH.match(raw) or not "2020-01" <= raw <= shift_month(current, 1):
        raise CollectionError(MONTH_ERROR)
    return raw


def board(office_id: int, month: str) -> list[CollectionItem]:
    return list(
        db.session.scalars(
            select(CollectionItem)
            .where(CollectionItem.office_id == office_id, CollectionItem.month == month)
            .order_by(CollectionItem.client_no)
        )
    )


def _lock_office(office_id: int) -> Office:
    office = db.session.scalar(
        select(Office)
        .where(Office.id == office_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if office is None or not office.is_active:
        db.session.rollback()
        raise CollectionNotFound
    return office


def _item(office_id: int, month: str, client_no: int) -> CollectionItem:
    item = db.session.scalar(
        select(CollectionItem).where(
            CollectionItem.office_id == office_id,
            CollectionItem.month == month,
            CollectionItem.client_no == client_no,
        )
    )
    if item is None:
        item = CollectionItem(
            office_id=office_id,
            month=month,
            client_no=client_no,
            is_ready=False,
            received_count=0,
        )
        db.session.add(item)
    return item


def _commit() -> None:
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise CollectionError(SAVE_ERROR) from None


def save_item(
    office_id: int, actor_id: int, month: str, raw_number: str | None, action: str
) -> None:
    """番号を足す（add）・そろった（ready）・まだに戻す（pending）。"""
    text = (raw_number or "").strip().translate(FULLWIDTH_DIGITS)
    if not (text.isascii() and text.isdigit() and len(text) <= 4):
        raise CollectionError(NUMBER_ERROR)
    client_no = int(text)
    if not 1 <= client_no <= NUMBER_MAX:
        raise CollectionError(NUMBER_ERROR)
    if action not in {"add", "ready", "pending"}:
        raise CollectionError("操作を確認できませんでした。")
    office = _lock_office(office_id)
    item = _item(office.id, month, client_no)
    if action != "add":
        item.is_ready = action == "ready"
    item.updated_by = actor_id
    record_audit(
        "collection_item_saved",
        actor_id=actor_id,
        office_id=office.id,
        target_type="collection",
        target_id=client_no,
    )
    _commit()


def carry_over(office_id: int, actor_id: int, month: str) -> int:
    """先月の番号を、この月に引き継ぐ（そろった・件数は引き継がない）。"""
    office = _lock_office(office_id)
    existing = {item.client_no for item in board(office.id, month)}
    added = 0
    for previous in board(office.id, shift_month(month, -1)):
        if previous.client_no not in existing:
            _item(office.id, month, previous.client_no).updated_by = actor_id
            added += 1
    if not added:
        db.session.rollback()
        raise CollectionError("先月の番号は、ありません。")
    record_audit("collection_carried_over", actor_id=actor_id, office_id=office.id)
    _commit()
    return added


def _digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def issue_token(office_id: int, actor_id: int) -> str:
    """受け取り用の合言葉を作る。前の合言葉は使えなくなる。保存するのはハッシュだけ。"""
    office = _lock_office(office_id)
    token = TOKEN_PREFIX + token_urlsafe(32)
    office.collection_token_hash = _digest(token)
    record_audit(
        "collection_token_issued",
        actor_id=actor_id,
        office_id=office.id,
        target_type="office",
        target_id=office.id,
    )
    _commit()
    return token


def office_for_token(office_id: int, token: str) -> Office | None:
    """合言葉が、その事務所のものかを確かめる。違えば None（理由は答えない）。"""
    office = db.session.get(Office, office_id)
    stored = office.collection_token_hash if office is not None else None
    # 事務所が無いときも同じだけ計算して比べる。
    matched = hmac.compare_digest(stored or "0" * 64, _digest(token))
    if office is None or not office.is_active or stored is None or not matched:
        return None
    return office


def _whole(value: object, low: int) -> int:
    # bool は int の仲間なので、型をそのまま確かめる。
    if type(value) is not int or not low <= value <= NUMBER_MAX:
        raise CollectionError("client_no は 1〜9999、received は 0〜9999 の整数です。")
    return value


def receive_counts(office_id: int, payload: object, today: date) -> int:
    """撮って送るだけ帳簿から、届いた件数だけを受け取る。

    知らないキーが1つでもあれば、何も保存しない（名前や金額を一緒に送れないように）。
    """
    if not isinstance(payload, dict) or set(payload) != {"month", "counts"}:
        raise CollectionError("受け取るのは month と counts だけです。")
    month = payload["month"]
    if not isinstance(month, str):
        raise CollectionError("month は YYYY-MM の形です。")
    try:
        month = parse_month(month, today)
    except CollectionError:
        raise CollectionError("month は YYYY-MM の形で、来月までです。") from None
    counts = payload["counts"]
    if not isinstance(counts, list) or not 1 <= len(counts) <= BATCH_MAX:
        raise CollectionError(f"counts は 1〜{BATCH_MAX}件の一覧です。")
    received: dict[int, int] = {}
    for entry in counts:
        if not isinstance(entry, dict) or set(entry) != {"client_no", "received"}:
            raise CollectionError("counts の中は client_no と received だけです。")
        client_no = _whole(entry["client_no"], 1)
        if client_no in received:
            raise CollectionError("同じ client_no が2回あります。")
        received[client_no] = _whole(entry["received"], 0)
    office = _lock_office(office_id)
    for client_no, count in sorted(received.items()):
        item = _item(office.id, month, client_no)
        item.received_count = count
        item.updated_by = None
    record_audit("collection_counts_received", office_id=office.id)
    _commit()
    return len(received)
