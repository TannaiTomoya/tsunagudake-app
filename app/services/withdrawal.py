from dataclasses import dataclass
from secrets import token_urlsafe

from sqlalchemy import and_, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from werkzeug.security import check_password_hash

from app.audit import record_audit
from app.extensions import db
from app.models import (
    BookkeepingLevel,
    CandidateStatus,
    Engagement,
    EngagementStatus,
    Invitation,
    MatchCandidate,
    MemberRole,
    MunicipalityMember,
    Need,
    NeedStatus,
    NeedTranslation,
    Office,
    OfficeMember,
    OutboxMail,
    PasswordResetToken,
    Reduction,
    Role,
    SecondFactor,
    Statement,
    StatementStatus,
    Talent,
    TalentAffiliation,
    TalentSkill,
    TrustItem,
    User,
    Visit,
)
from app.ratelimit import Bucket, check_login, finish_login, login_buckets
from app.services.messages import erase_for_office, erase_sent_by

WITHDRAWN_NAME = "退会した人"
REMOVED_TEXT = "（退会により消しました）"


class WithdrawalError(ValueError):
    """入力の誤り（パスワード・確認の印）。"""


class WithdrawalBlocked(Exception):
    """退会の前に、終えることが残っている。"""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("、".join(reasons))
        self.reasons = reasons


def _open_engagements(*where: object) -> bool:
    return (
        db.session.scalar(
            select(Engagement.id)
            .where(Engagement.status != EngagementStatus.ENDED, *where)  # type: ignore[arg-type]
            .limit(1)
        )
        is not None
    )


def _open_months(*where: object) -> bool:
    """減った時間の記録があるのに、明細が確定していない月があるか。"""
    return (
        db.session.scalar(
            select(Reduction.id)
            .join(Engagement, Engagement.id == Reduction.engagement_id)
            .outerjoin(
                Statement,
                and_(
                    Statement.engagement_id == Reduction.engagement_id,
                    Statement.month == Reduction.month,
                    Statement.status == StatementStatus.FINALIZED,
                ),
            )
            .where(Statement.id.is_(None), *where)  # type: ignore[arg-type]
            .limit(1)
        )
        is not None
    )


def _sole_owner_office(user: User) -> Office | None:
    """この人が、ただ1人の代表である事務所（退会すると、事務所も閉じる）。"""
    row = db.session.execute(
        select(Office, OfficeMember.member_role)
        .join(OfficeMember, OfficeMember.office_id == Office.id)
        .where(OfficeMember.user_id == user.id, Office.is_active.is_(True))
    ).one_or_none()
    if row is None or row[1] != MemberRole.OWNER:
        return None
    office: Office = row[0]
    others = db.session.scalar(
        select(OfficeMember.id)
        .join(User, User.id == OfficeMember.user_id)
        .where(
            OfficeMember.office_id == office.id,
            OfficeMember.user_id != user.id,
            OfficeMember.member_role == MemberRole.OWNER,
            User.is_active.is_(True),
        )
        .limit(1)
    )
    return office if others is None else None


@dataclass(frozen=True)
class _Held:
    """ロックして読み直した、つなぎ手の候補と伴走（今の値）。"""

    candidates: list[MatchCandidate]
    engagements: list[Engagement]


def _hold_talent_rows(user_id: int) -> _Held | None:
    """声かけ・返事と同じく、候補を先にロックし、候補と伴走の今の値を読む。

    人（users）の行は、この後でロックする。先に人をロックすると、声かけの最後の
    通知と待ち合う。ロックなしの読み取りは、MySQL では処理の始めの時点の値なので、
    直前に保存された声かけ・合意を見落とす。
    """
    talent_id = db.session.scalar(select(Talent.id).where(Talent.user_id == user_id))
    if talent_id is None:
        return None
    candidates = db.session.scalars(
        select(MatchCandidate)
        .where(MatchCandidate.talent_id == talent_id)
        .order_by(MatchCandidate.id)
        .with_for_update()
    ).all()
    engagements = db.session.scalars(
        select(Engagement)
        .where(Engagement.talent_id == talent_id)
        .order_by(Engagement.id)
        .with_for_update()
    ).all()
    return _Held(list(candidates), list(engagements))


def blockers(user: User, held: _Held | None = None) -> list[str]:
    """退会できない理由。空なら退会できる。

    `held` は、退会の処理がロックして読み直した行。画面に出すだけのときは渡さない。
    """
    if user.role == Role.ADMIN:
        return ["運営のアカウントは、この画面から退会できません。"]
    if user.is_demo:
        return ["デモのアカウントは、みんなで使うので退会できません。"]
    reasons: list[str] = []
    if user.role == Role.TSUNAGITE:
        talent = db.session.scalar(select(Talent).where(Talent.user_id == user.id))
        if talent is not None:
            mine = Engagement.talent_id == talent.id
            if held is None:
                engaged = _open_engagements(mine)
                invited = (
                    db.session.scalar(
                        select(MatchCandidate.id)
                        .where(
                            MatchCandidate.talent_id == talent.id,
                            MatchCandidate.status == CandidateStatus.INVITED,
                        )
                        .limit(1)
                    )
                    is not None
                )
            else:
                engaged = any(
                    row.status != EngagementStatus.ENDED for row in held.engagements
                )
                invited = any(
                    row.status == CandidateStatus.INVITED for row in held.candidates
                )
            if engaged:
                reasons.append("進行中の伴走があります。先に、伴走を終えてください。")
            if _open_months(mine):
                reasons.append(
                    "確定していない明細があります。事務所の確定を待ってください。"
                )
            if invited:
                reasons.append(
                    "返事を待っている声かけがあります。受けるか断るかを選んでください。"
                )
    elif user.role == Role.OFFICE:
        office = _sole_owner_office(user)
        if office is not None:
            mine = Engagement.office_id == office.id
            if _open_engagements(mine):
                reasons.append("進行中の伴走があります。先に、伴走を終えてください。")
            if _open_months(mine):
                reasons.append(
                    "確定していない明細があります。先に、明細を確定してください。"
                )
            published = db.session.scalar(
                select(Need.id)
                .where(Need.office_id == office.id, Need.status == NeedStatus.PUBLISHED)
                .limit(1)
            )
            if published is not None:
                reasons.append("公開中の困りごとがあります。先に、取り下げてください。")
            staff = db.session.scalar(
                select(OfficeMember.id)
                .join(User, User.id == OfficeMember.user_id)
                .where(
                    OfficeMember.office_id == office.id,
                    OfficeMember.user_id != user.id,
                    User.is_active.is_(True),
                )
                .limit(1)
            )
            if staff is not None:
                reasons.append(
                    "事務所に、ほかの人のアカウントがあります。運営にご連絡ください。"
                )
    return reasons


def closes_office(user: User) -> bool:
    return user.role == Role.OFFICE and _sole_owner_office(user) is not None


def _erase_talent(user: User, held: _Held) -> None:
    talent = db.session.scalar(
        select(Talent)
        .where(Talent.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if talent is None:
        return
    # 業務の表は、1行ずつ消す。
    for kind in (TalentSkill, TrustItem):
        for row in db.session.scalars(select(kind).where(kind.talent_id == talent.id)):
            db.session.delete(row)
    # ロックして読み直した行を使う（直前に出し直された候補も、終了にする）。
    for candidate in held.candidates:
        if candidate.status == CandidateStatus.LISTED:
            candidate.status = CandidateStatus.CLOSED
    statements = db.session.scalars(
        select(Statement)
        .join(Engagement, Engagement.id == Statement.engagement_id)
        .where(Engagement.talent_id == talent.id)
    )
    for statement in statements:
        # 金額・月・状態は相手の記録でもあるので残す。振込先と登録番号だけを消す。
        statement.payout_cipher = None
        statement.invoice_registration = None
    if not held.candidates and not held.engagements:
        db.session.flush()
        db.session.delete(talent)
        return
    # 候補・伴走が指している行は消せないので、中身だけを消す。
    talent.payout_cipher = None
    talent.invoice_registration = None
    talent.is_listed = False
    talent.weekdays_mask = 0
    talent.capacity = 1
    talent.bookkeeping_level = BookkeepingLevel.NONE
    talent.affiliation = TalentAffiliation.NONE
    talent.level_verified_at = talent.level_verified_by = None
    talent.translation_verified_at = talent.translation_verified_by = None


def _close_office(office_id: int) -> None:
    office = db.session.scalar(
        select(Office)
        .where(Office.id == office_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if office is None:
        return
    office.is_active = False
    office.client_count = None
    office.address = None
    office.lat_e6 = office.lon_e6 = None
    # 件数の受け取りも止める。
    office.collection_token_hash = None
    for need in db.session.scalars(select(Need).where(Need.office_id == office.id)):
        need.body = REMOVED_TEXT
        need.block_reason = None
        for translation in db.session.scalars(
            select(NeedTranslation).where(NeedTranslation.need_id == need.id)
        ):
            translation.evidence = ""
    visits = db.session.scalars(
        select(Visit)
        .join(Engagement, Engagement.id == Visit.engagement_id)
        .where(Engagement.office_id == office.id)
    )
    for visit in visits:
        visit.note = None
        visit.learned_term = None
        visit.idea = None


def withdraw(user_id: int, password: str, *, confirmed: bool) -> None:
    """退会する。行は残し、名前とメールアドレスを消す（台帳と明細が指しているため）。"""
    # 誤ったパスワードを、人ごとに数える（メールアドレスは鍵に使わない）。
    buckets = login_buckets(f"user-{user_id}", namespace="withdraw")
    check_login(buckets)
    try:
        _withdraw(user_id, password, confirmed, buckets)
    except DataError, IntegrityError, OperationalError:
        db.session.rollback()
        raise WithdrawalBlocked(
            ["保存できませんでした。時間をおいて、もう一度お試しください。"]
        ) from None


def _withdraw(
    user_id: int, password: str, confirmed: bool, buckets: tuple[Bucket, ...]
) -> None:
    held = _hold_talent_rows(user_id)
    user = db.session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        db.session.rollback()
        raise WithdrawalError("アカウントを確認できませんでした。")
    if not confirmed or not check_password_hash(user.password_hash, password):
        finish_login(buckets, successful=False)
        db.session.commit()
        raise WithdrawalError(
            "パスワードと、「元に戻せないことを確かめました」の印をご確認ください。"
        )
    reasons = blockers(user, held)
    if reasons:
        db.session.rollback()
        raise WithdrawalBlocked(reasons)
    office = _sole_owner_office(user) if user.role == Role.OFFICE else None

    if user.role == Role.TSUNAGITE and held is not None:
        _erase_talent(user, held)
    if office is not None:
        _close_office(office.id)
        erase_for_office(office.id)
    erase_sent_by(user.id)
    for kind in (OfficeMember, MunicipalityMember, PasswordResetToken):
        for row in db.session.scalars(select(kind).where(kind.user_id == user.id)):
            db.session.delete(row)
    factor = db.session.get(SecondFactor, user.id)
    if factor is not None:
        db.session.delete(factor)
    anonymous = f"withdrawn-{user.id}@withdrawn.invalid"
    # 送る前のメールは、本文を消すと送られない。
    # 直前に積まれた通知も見落とさないよう、ロックして今の行を読む。
    for mail in db.session.scalars(
        select(OutboxMail).where(OutboxMail.recipient == user.email).with_for_update()
    ):
        mail.recipient = anonymous
        mail.body_encrypted = None
        mail.digest_key = None
    for invitation in db.session.scalars(
        select(Invitation).where(Invitation.email == user.email)
    ):
        invitation.email = anonymous
    user.email = anonymous
    user.display_name = WITHDRAWN_NAME
    user.is_active = False
    user.set_password(token_urlsafe(32))
    record_audit(
        "account_withdrawn",
        actor_id=user.id,
        office_id=office.id if office is not None else None,
    )
    db.session.commit()
