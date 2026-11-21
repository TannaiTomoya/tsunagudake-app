"""外の AI を呼ぶ、ただ1つの場所。既定は使わない（AI_DRIVER=off）。

出すのは候補だけで、決めるのは人。候補探し・料金・明細・台帳・税理士業務の見分けには使わない。
送るのは、名前・社名・金額・連絡先の形を伏せた後の文だけ。本文も、返ってきた文も残さない。
"""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    AiOutcome,
    AiPurpose,
    AiRun,
    Need,
    today_in_japan,
    utc_now,
)
from tsunagu_rules.redact import redact
from tsunagu_rules.types import NeedKind

SEND_MAX = 1000
REPLY_TOKENS = 200
TIMEOUT_SECONDS = 10
KIND_MAX = 2
PERSON_DAILY_MAX = 20
ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
REQUIRED = (
    "AI_MODEL",
    "AI_API_KEY",
    "AI_MONTHLY_LIMIT_YEN",
    "AI_INPUT_YEN_PER_MTOK",
    "AI_OUTPUT_YEN_PER_MTOK",
)

TRANSLATE_SYSTEM = (
    "会計事務所が書いた困りごとを、次の型に分けます。"
    "collect=資料回収、check=チェックと比較、entry=入力・取り込み、"
    "report=月次の資料づくり、tooling=道具の設定、teaching=使い方を教える。"
    "当てはまる型の英字の値を、多くても2つ、JSON の配列だけで返してください。"
    "当てはまらなければ [] を返してください。"
    "説明・助言・税務の判断は書かないでください。"
)

# テストと手元の確認で使う、決まった返事。Enum に無い値は捨てられる。
FAKE_REPLIES = {AiPurpose.TRANSLATE: '["tooling", "audit"]'}


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int
    output_tokens: int


def _fake(purpose: AiPurpose, system: str, text: str) -> Reply:
    reply = FAKE_REPLIES[purpose]
    return Reply(reply, len(system) + len(text), len(reply))


def _claude(purpose: AiPurpose, system: str, text: str) -> Reply:
    config = current_app.config
    request = urllib.request.Request(  # noqa: S310
        ENDPOINT,
        data=json.dumps(
            {
                "model": config["AI_MODEL"],
                "max_tokens": REPLY_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": text}],
            }
        ).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": config["AI_API_KEY"],
            "anthropic-version": API_VERSION,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        payload = json.loads(response.read(200_000))
    usage = payload["usage"]
    return Reply(
        "".join(
            part["text"] for part in payload["content"] if part.get("type") == "text"
        ),
        int(usage["input_tokens"]),
        int(usage["output_tokens"]),
    )


DRIVERS: dict[str, Callable[[AiPurpose, str, str], Reply]] = {
    "fake": _fake,
    "claude": _claude,
}


def month_cost_yen(today: date | None = None) -> int:
    """今月（日本時間）に使った量 × 単価。端数は月の合計で切り捨てる。"""
    day = today or today_in_japan()
    start = datetime(day.year, day.month, 1) - timedelta(hours=9)
    sent, received = db.session.execute(
        select(
            func.coalesce(func.sum(AiRun.input_tokens), 0),
            func.coalesce(func.sum(AiRun.output_tokens), 0),
        ).where(AiRun.created_at >= start)
    ).one()
    config = current_app.config
    yen_per_million = int(sent) * int(config["AI_INPUT_YEN_PER_MTOK"]) + int(
        received
    ) * int(config["AI_OUTPUT_YEN_PER_MTOK"])
    return yen_per_million // 1_000_000


def available() -> bool:
    """いま外の AI を呼べるか。off・設定の不足・月の上限のどれかなら、呼ばない。"""
    config = current_app.config
    driver = config["AI_DRIVER"]
    if driver not in DRIVERS:
        return False
    if driver == "claude" and not all(config[name] for name in REQUIRED):
        return False
    limit = config["AI_MONTHLY_LIMIT_YEN"]
    return not limit or month_cost_yen() < limit


def _within_daily_count(actor_id: int) -> bool:
    count = db.session.scalar(
        select(func.count())
        .select_from(AiRun)
        .where(
            AiRun.actor_user_id == actor_id,
            AiRun.created_at >= utc_now() - timedelta(hours=24),
        )
    )
    return (count or 0) < PERSON_DAILY_MAX


def _ask(
    purpose: AiPurpose,
    system: str,
    text: str,
    *,
    actor_id: int,
    office_id: int | None,
) -> tuple[AiRun, str] | None:
    """伏せた後の文だけを送る。返すのは、まだ保存していない記録の行と返事。

    呼べないときは None。
    """
    if not available() or not _within_daily_count(actor_id):
        return None
    masked = redact(text)[:SEND_MAX].strip()
    if not masked:
        return None
    driver = current_app.config["AI_DRIVER"]
    reply: Reply | None
    try:
        reply = DRIVERS[driver](purpose, system, masked)
    except (
        OSError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        # 鍵や本文がログに出ないよう、種類だけを残す。
        current_app.logger.warning(
            "AI の呼び出しに失敗しました（%s）。", type(error).__name__
        )
        reply = None
    run = AiRun(
        actor_user_id=actor_id,
        office_id=office_id,
        purpose=purpose,
        driver=driver,
        chars=len(masked),
        input_tokens=reply.input_tokens if reply else 0,
        output_tokens=reply.output_tokens if reply else 0,
        outcome=AiOutcome.OK if reply else AiOutcome.ERROR,
    )
    db.session.add(run)
    return run, reply.text if reply else ""


def _kinds(text: str) -> list[NeedKind]:
    """返事から、Enum にある型だけを採る。無い値・形の違う返事は捨てる。"""
    match = re.search(r"\[[^\[\]]*\]", text)
    try:
        items = json.loads(match.group()) if match else []
    except ValueError:
        items = []
    kinds: list[NeedKind] = []
    for item in items:
        if isinstance(item, str) and item in NeedKind and NeedKind(item) not in kinds:
            kinds.append(NeedKind(item))
    return kinds[:KIND_MAX]


def suggestion_for(need: Need) -> list[NeedKind] | None:
    """この困りごとの、いまの文に対する直近の候補。まだ頼んでいなければ None。"""
    run = db.session.scalar(
        select(AiRun)
        .where(
            AiRun.need_id == need.id,
            AiRun.purpose == AiPurpose.TRANSLATE,
            AiRun.input_hash == need.input_hash,
            AiRun.outcome != AiOutcome.ERROR,
        )
        .order_by(AiRun.id.desc())
        .limit(1)
    )
    if run is None:
        return None
    return [NeedKind(value) for value in (run.result or "").split(",") if value]


def suggest_kinds(need: Need, actor_id: int) -> AiOutcome | None:
    """型の候補を頼み、記録の行を足す（保存は呼んだ側）。呼べなかったら None。"""
    asked = _ask(
        AiPurpose.TRANSLATE,
        TRANSLATE_SYSTEM,
        need.body,
        actor_id=actor_id,
        office_id=need.office_id,
    )
    if asked is None:
        return None
    run, text = asked
    run.need_id = need.id
    run.input_hash = need.input_hash
    if run.outcome == AiOutcome.OK:
        kinds = _kinds(text)
        run.result = ",".join(kind.value for kind in kinds) or None
        if not kinds:
            run.outcome = AiOutcome.EMPTY
    return run.outcome
