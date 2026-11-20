from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.audit import record_audit
from app.authz import require_need
from app.extensions import db
from app.models import (
    ItTask,
    Need,
    NeedStatus,
    NeedTranslation,
    Office,
    TranslationSource,
    utc_now,
)
from app.services.ledger import append_record
from tsunagu_rules import RULES_VERSION
from tsunagu_rules.translation import load_dictionary, translate
from tsunagu_rules.types import NeedKind, Pattern

DICTIONARY = load_dictionary()
EDITABLE = {NeedStatus.DRAFT, NeedStatus.BLOCKED}


class NeedConflict(Exception):
    pass


@dataclass(frozen=True)
class NeedInput:
    body: str
    monthly_hours: int
    weekdays_mask: int
    manual_kind: NeedKind | None


def translations(need: Need, *, lock: bool = False) -> list[NeedTranslation]:
    query = (
        select(NeedTranslation)
        .where(NeedTranslation.need_id == need.id)
        .order_by(NeedTranslation.id)
    )
    if lock:
        query = query.with_for_update()
    return list(db.session.scalars(query))


def _commit() -> None:
    try:
        db.session.commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise NeedConflict(
            "保存できませんでした。内容を確認してもう一度お試しください。"
        ) from None


def save_need(
    office: Office, actor_id: int, data: NeedInput, need_id: int | None = None
) -> Need:
    if need_id is None:
        need = Need(office_id=office.id, created_by=actor_id)
        db.session.add(need)
    else:
        need = require_need(office, need_id, lock=True)
        if need.status not in EDITABLE:
            raise NeedConflict("この困りごとは書き直せません。")

    result = translate(data.body, DICTIONARY)
    need.body = data.body
    need.monthly_hours = data.monthly_hours
    need.weekdays_mask = data.weekdays_mask
    need.status = NeedStatus.BLOCKED if result.blocked else NeedStatus.DRAFT
    need.block_reason = "・".join(result.blocked_words)[:200] or None
    need.rules_version = RULES_VERSION
    need.input_hash = result.input_hash
    patterns = result.patterns
    source = TranslationSource.RULE
    if not result.blocked and not patterns and data.manual_kind:
        pattern = next(
            item for item in DICTIONARY.patterns if item.kind == data.manual_kind
        )
        patterns = (Pattern(pattern.kind, (), pattern.tasks),)
        source = TranslationSource.MANUAL

    try:
        if need_id is not None:
            for row in translations(need, lock=True):
                db.session.delete(row)
        db.session.flush()
        for pattern in patterns:
            for task in pattern.tasks:
                db.session.add(
                    NeedTranslation(
                        need_id=need.id,
                        category=pattern.kind,
                        it_task=ItTask(task),
                        evidence="・".join(pattern.words)[:200]
                        or "手動で選択しました。",
                        source=source,
                    )
                )
        _commit()
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise NeedConflict(
            "保存できませんでした。内容を確認してもう一度お試しください。"
        ) from None
    return need


def publish_need(office: Office, need_id: int, actor_id: int) -> None:
    need = require_need(office, need_id, lock=True)
    if need.status == NeedStatus.BLOCKED:
        raise NeedConflict(
            "税理士業務にあたる可能性があるため公開できません。頼みたい作業をご確認ください。"
        )
    if need.status != NeedStatus.DRAFT or not translations(need, lock=True):
        raise NeedConflict("公開するには下書きの型を選んでください。")
    need.status = NeedStatus.PUBLISHED
    need.published_by = actor_id
    need.published_at = utc_now()
    need.value_yen_month = need.monthly_hours * office.hourly_rate_yen
    append_record(
        need, "published", actor_id, {"value_yen_month": need.value_yen_month}
    )
    record_audit(
        "need_published",
        actor_id=actor_id,
        office_id=office.id,
        target_type="need",
        target_id=need.id,
    )
    _commit()


def withdraw_need(office: Office, need_id: int, actor_id: int) -> None:
    need = require_need(office, need_id, lock=True)
    if need.status != NeedStatus.PUBLISHED:
        raise NeedConflict("公開中の困りごとだけ取り下げられます。")
    need.status = NeedStatus.WITHDRAWN
    record_audit(
        "need_withdrawn",
        actor_id=actor_id,
        office_id=office.id,
        target_type="need",
        target_id=need.id,
    )
    _commit()
