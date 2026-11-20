import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.extensions import db
from app.models import (
    Engagement,
    LedgerAnchor,
    LedgerRecord,
    Office,
    Role,
    Talent,
    User,
)
from app.services.ledger import ledger_failures
from app.services.mail import enqueue, notify_office, notify_user
from tsunagu_rules import ledger

Tip = tuple[int, int, str]
FILE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.json\Z")


@dataclass(frozen=True)
class AnchorResult:
    day: date
    chains: int
    entries: int
    digest: str
    problems: list[str]
    created: bool


def _read(path: Path) -> list[Tip] | None:
    """控えのファイルを読む。壊れている・書き換えられているなら None。"""
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        tips = [
            (int(need), int(seq), str(mark)) for need, seq, mark in content["chains"]
        ]
        if content["digest"] != ledger.anchor_digest(tips):
            return None
    except OSError, KeyError, TypeError, ValueError:
        return None
    return tips


def _previous(directory: Path, today: date) -> Path | None:
    name = f"{today.isoformat()}.json"
    older = sorted(
        path
        for path in directory.iterdir()
        if FILE_NAME.match(path.name) and path.name < name
    )
    return older[-1] if older else None


def anchor_ledger(directory: Path, today: date) -> AnchorResult:
    """全部の鎖を検証し、鎖ごとの最後のハッシュを DB の外（ファイル）に控える。

    鎖を最後まで作り直す書き換えは、鎖の検証では見つからない。
    前に控えた行が、今も同じハッシュかを比べて見つける。
    """
    problems = [
        f"困りごと {need_id} / 行 {seq}：鎖が合いません"
        for need_id, seq in ledger_failures()
    ]
    # デモの台帳は reset-demo で作り直すので、控えない。
    rows = db.session.execute(
        select(LedgerRecord.need_id, LedgerRecord.seq, LedgerRecord.entry_hash)
        .join(Office, Office.id == LedgerRecord.office_id)
        .where(Office.is_demo.is_(False))
        .order_by(LedgerRecord.need_id, LedgerRecord.seq)
    ).all()
    current = {(need, seq): mark for need, seq, mark in rows}
    last: dict[int, Tip] = {need: (need, seq, mark) for need, seq, mark in rows}
    tips = sorted(last.values())
    digest = ledger.anchor_digest(tips)

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    before: dict[int, Tip] = {}
    previous = _previous(directory, today)
    recorded = db.session.scalar(
        select(LedgerAnchor)
        .where(LedgerAnchor.anchored_on < today)
        .order_by(LedgerAnchor.anchored_on.desc())
        .limit(1)
    )
    if previous is None:
        if recorded is not None:
            problems.append("前の控えのファイルがありません")
    else:
        anchored = _read(previous)
        if anchored is None:
            problems.append(f"控えのファイル {previous.name} を読めません")
        else:
            before = {need: (need, seq, mark) for need, seq, mark in anchored}
            problems += [
                f"困りごと {need_id}：{previous.stem} の控えと合いません"
                for need_id in ledger.rewritten_chains(anchored, current)
            ]
            day = date.fromisoformat(previous.stem)
            row = db.session.scalar(
                select(LedgerAnchor).where(LedgerAnchor.anchored_on == day)
            )
            if row is not None and row.digest != ledger.anchor_digest(anchored):
                problems.append(f"{previous.stem} の控えが、ファイルと DB で違います")

    created = False
    target = directory / f"{today.isoformat()}.json"
    if not target.exists():
        content = {"date": today.isoformat(), "digest": digest, "chains": tips}
        # 上書きしない（同じ日にもう一度動かしても、最初の控えを残す）。
        with target.open("x", encoding="utf-8") as stream:
            json.dump(content, stream, separators=(",", ":"))
        target.chmod(0o600)
        created = True
    row = db.session.scalar(
        select(LedgerAnchor).where(LedgerAnchor.anchored_on == today)
    )
    if row is None:
        db.session.add(
            LedgerAnchor(
                anchored_on=today,
                chains=len(tips),
                entries=len(rows),
                digest=digest,
                problems=len(problems),
            )
        )
    else:
        row.problems = max(row.problems, len(problems))
    if created:
        for tip in tips:
            if before.get(tip[0]) != tip:
                _tell_parties(tip)
    if problems:
        body = "\n".join(
            ["境界線台帳の毎日の検証で、合わない記録が見つかりました。", *problems]
        )
        for admin in db.session.scalars(
            select(User)
            .where(User.role == Role.ADMIN, User.is_active.is_(True))
            .order_by(User.id)
        ):
            enqueue(
                admin.email,
                "つなぐだけ：台帳の検証で不一致が見つかりました",
                body,
                demo=admin.is_demo,
            )
    db.session.commit()
    return AnchorResult(today, len(tips), len(rows), digest, problems, created)


def _tell_parties(tip: Tip) -> None:
    """鎖が伸びた困りごとの双方に、最後のハッシュを知らせる（DB の外の控え）。"""
    need_id, seq, mark = tip
    subject = "境界線台帳の控え"
    body = f"困りごと {need_id} の台帳は {seq} 行、最後の印は {mark} です"
    office_id = db.session.scalar(
        select(LedgerRecord.office_id).where(LedgerRecord.need_id == need_id).limit(1)
    )
    if office_id is not None:
        notify_office(office_id, subject, body, owners_only=True)
    for person in db.session.scalars(
        select(User)
        .join(Talent, Talent.user_id == User.id)
        .join(Engagement, Engagement.talent_id == Talent.id)
        .where(Engagement.need_id == need_id)
        .order_by(User.id)
    ):
        notify_user(person, subject, body)


def latest_anchor() -> LedgerAnchor | None:
    return db.session.scalar(
        select(LedgerAnchor).order_by(LedgerAnchor.anchored_on.desc()).limit(1)
    )
