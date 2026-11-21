from sqlalchemy import select

from app.audit import record_audit
from app.extensions import db
from app.models import Office

ACCOUNTING_SOFTWARE = {
    "freee": "freee会計",
    "mf": "マネーフォワード クラウド会計",
    "yayoi": "弥生会計",
    "excel": "Excel",
    "sheets": "Google スプレッドシート",
}

CLIENT_COUNT_MAX = 100_000
CLIENT_COUNT_ERROR = "顧問先の数は、0〜100,000の整数で入力してください。"
# 全角の数字だけを半角に直す（丸数字や上付きの数字は数として受け取らない）。
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


class OfficeProfileError(ValueError):
    pass


class OfficeNotFound(LookupError):
    pass


def save_client_count(office_id: int, actor_id: int, raw: str | None) -> None:
    """顧問先の数を入れる。空は「入力していない」に戻す。"""
    if raw is None:
        raise OfficeProfileError(CLIENT_COUNT_ERROR)
    text = raw.strip().translate(FULLWIDTH_DIGITS)
    count = None
    if text:
        if not (text.isascii() and text.isdigit() and len(text) <= 6):
            raise OfficeProfileError(CLIENT_COUNT_ERROR)
        count = int(text)
        if count > CLIENT_COUNT_MAX:
            raise OfficeProfileError(CLIENT_COUNT_ERROR)
    office = _locked_office(office_id)
    office.client_count = count
    record_audit(
        "office_client_count_saved",
        actor_id=actor_id,
        office_id=office.id,
        target_type="office",
        target_id=office.id,
    )
    db.session.commit()


def _locked_office(office_id: int) -> Office:
    office = db.session.scalar(
        select(Office)
        .where(Office.id == office_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if office is None or not office.is_active:
        db.session.rollback()
        raise OfficeNotFound
    return office


def save_software(office_id: int, actor_id: int, software: str | None) -> None:
    if software != "" and software not in ACCOUNTING_SOFTWARE:
        raise OfficeProfileError("一覧から会計ソフトを選んでください。")
    office = _locked_office(office_id)
    office.software = software or None
    record_audit(
        "office_software_saved",
        actor_id=actor_id,
        office_id=office.id,
        target_type="office",
        target_id=office.id,
    )
    db.session.commit()
