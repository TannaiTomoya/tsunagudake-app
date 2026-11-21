"""外の AI を呼ぶ、ただ1つの場所。既定は使わない（AI_DRIVER=off）。

出すのは候補だけで、決めるのは人。候補探し・料金・明細・台帳・税理士業務の見分けには使わない。
送るのは、名前・社名・金額・連絡先の形を伏せた後の文だけ。本文も、返ってきた文も残さない。
"""

import hashlib
import json
import re
import unicodedata
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
    Engagement,
    Need,
    today_in_japan,
    utc_now,
)
from tsunagu_rules.redact import redact
from tsunagu_rules.types import NeedKind

SEND_MAX = 1000
REPLY_TOKENS = 200
# 下書きは文が長いので、返事の上限を用途ごとに決める（文字の上限より少し多め）。
DRAFT_TOKENS = {"draft": 700, "question": 300, "summary": 500}
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

DRAFT_SYSTEM = (
    "会計事務所と、週に1回来る「つなぎ手」の間の連絡文を整えます。"
    "次のメモを、ていねいな日本語の連絡文にしてください。400文字以内。"
    "メモに無い事実・日時・金額・名前を足さないでください。"
    "「〈伏せ〉」は、そのまま残してください。"
    "税務の判断や助言は書かないでください。連絡文だけを返してください。"
)
BRIEF_SYSTEM = (
    "会計事務所がIT担当者に相談する文章の下書きを作ります。"
    "今行っている作業と、手伝ってほしいことを、短い日本語でまとめてください。"
    "入力にない製品名・実績・数字・期限・連携の可否を作らないでください。"
    "人の推薦、料金の決定、税務の助言はせず、〈伏せ〉はそのままにします。"
    "本文だけを400文字以内で返してください。"
)
QUESTION_SYSTEM = (
    "「つなぐだけ」の案内役として答えます。つなぐだけは、地方の会計事務所に、"
    "会計とITがわかる人（つなぎ手）を週1回入れるしくみです。"
    "選ぶ・合意する・確かめる・確定するのは人で、減らなければ料金は0円です。"
    "下の「用意した答え」だけをもとに、敬体で、150文字以内で答えてください。"
    "そこに無いことは「用意した答えにありません。」とだけ答えてください。"
    "税務・会計の判断や助言を求められたら"
    "「税務の判断は、税理士の仕事です。」とだけ答えてください。"
)
SUMMARY_SYSTEM = (
    "会計事務所への訪問で、つなぎ手が気づいたことのメモです。"
    "似たものをまとめ、箇条書きで3つまで、あわせて300文字以内にしてください。"
    "メモに無いことを足さないでください。「〈伏せ〉」は、そのまま残してください。"
    "税務の判断や助言は書かないでください。"
)
REPLY_MAX = {
    AiPurpose.DRAFT: 400,
    AiPurpose.QUESTION: 160,
    AiPurpose.SUMMARY: 300,
}
ANSWER_CACHE_MAX = 200
AUTO_ANSWER = "（自動で作った答えです）"

# テストと手元の確認で使う、決まった返事。Enum に無い値は捨てられる。
FAKE_REPLIES = {
    AiPurpose.TRANSLATE: '["tooling", "audit"]',
    AiPurpose.DRAFT: "お世話になっております。架空の下書きです。",
    AiPurpose.QUESTION: "架空の答えです。",
    AiPurpose.SUMMARY: "・架空のまとめです。",
}

# 同じ質問には、前の答えを使い回す。持つのは、伏せた後の質問のハッシュと答えだけ。
# メモリの中だけで、DB にもログにも残さない。プロセスが止まれば消える。
_answers: dict[str, str] = {}


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
                "max_tokens": DRAFT_TOKENS.get(purpose.value, REPLY_TOKENS),
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


def month_cost_yen(today: date | None = None, *, office_id: int | None = None) -> int:
    """今月（日本時間）に使った量 × 単価。端数は月の合計で切り捨てる。

    `office_id` を渡すと、その事務所の分だけを数える。
    """
    day = today or today_in_japan()
    start = datetime(day.year, day.month, 1) - timedelta(hours=9)
    query = select(
        func.coalesce(func.sum(AiRun.input_tokens), 0),
        func.coalesce(func.sum(AiRun.output_tokens), 0),
    ).where(AiRun.created_at >= start)
    if office_id is not None:
        query = query.where(AiRun.office_id == office_id)
    sent, received = db.session.execute(query).one()
    config = current_app.config
    yen_per_million = int(sent) * int(config["AI_INPUT_YEN_PER_MTOK"]) + int(
        received
    ) * int(config["AI_OUTPUT_YEN_PER_MTOK"])
    return yen_per_million // 1_000_000


def available(office_id: int | None = None) -> bool:
    """いま外の AI を呼べるか。off・設定の不足・月の上限のどれかなら、呼ばない。

    `office_id` を渡すと、その事務所の月の上限も確かめる。
    """
    config = current_app.config
    driver = config["AI_DRIVER"]
    if driver not in DRIVERS:
        return False
    if driver == "claude" and not all(config[name] for name in REQUIRED):
        return False
    limit = config["AI_MONTHLY_LIMIT_YEN"]
    if limit and month_cost_yen() >= limit:
        return False
    share = config["AI_OFFICE_MONTHLY_LIMIT_YEN"]
    return not (
        share and office_id is not None and month_cost_yen(office_id=office_id) >= share
    )


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
    if not available(office_id) or not _within_daily_count(actor_id):
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


def _plain(text: str, limit: int) -> str:
    """返ってきた文を、画面に出せる形にする。制御文字を落とし、上限で切る。"""
    kept = "".join(
        letter
        for letter in text.replace("\r\n", "\n").replace("\r", "\n")
        if letter == "\n" or unicodedata.category(letter)[0] != "C"
    )
    return kept.strip()[:limit].strip()


def _write(
    purpose: AiPurpose, system: str, text: str, *, actor_id: int, office_id: int | None
) -> str | None:
    """下書きを1つ作って、記録の行を保存する。作れなかったら None。

    返ってきた文は、呼んだ側が画面に出すだけで、ここでは残さない。
    """
    asked = _ask(purpose, system, text, actor_id=actor_id, office_id=office_id)
    if asked is None:
        return None
    run, reply = asked
    written = _plain(reply, REPLY_MAX[purpose])
    if run.outcome == AiOutcome.OK and not written:
        run.outcome = AiOutcome.EMPTY
    db.session.commit()
    return written or None


def draft_brief(office_id: int, actor_id: int, memo: str) -> str | None:
    return _write(
        AiPurpose.DRAFT, BRIEF_SYSTEM, memo, actor_id=actor_id, office_id=office_id
    )


def draft_message(
    engagement: Engagement, actor_id: int, memo: str, topic: str
) -> str | None:
    """入力欄のメモから、メッセージの下書きを作る。送るのは人。

    送るのは、メモを伏せた後の文と、ひな形の名前だけ。
    これまでのメッセージ・困りごとの本文・名前は送らない。
    """
    text = f"用件：{topic}\nメモ：{memo}" if topic else f"メモ：{memo}"
    return _write(
        AiPurpose.DRAFT,
        DRAFT_SYSTEM,
        text,
        actor_id=actor_id,
        office_id=engagement.office_id,
    )


def summarize_ideas(
    engagement: Engagement, actor_id: int, ideas: list[str]
) -> str | None:
    """今月のメモ（気づいたこと）の、まとめの下書きを作る。保存しない。"""
    return _write(
        AiPurpose.SUMMARY,
        SUMMARY_SYSTEM,
        "\n".join(f"・{idea}" for idea in ideas),
        actor_id=actor_id,
        office_id=engagement.office_id,
    )


def answer_question(
    actor_id: int, question: str, known: list[tuple[str, str]]
) -> str | None:
    """用意した答えに無い質問に、近い答えをもとにした答えを作る。

    `known` は、あらかじめ書いた質問と答え（公開している文）。
    同じ質問には、外を呼ばずに前の答えを返す。
    """
    key = hashlib.sha256(redact(question).encode()).hexdigest()
    if key in _answers:
        return _answers[key]
    context = "\n".join(f"問：{asked}\n答：{answer}" for asked, answer in known)
    written = _write(
        AiPurpose.QUESTION,
        f"{QUESTION_SYSTEM}\n用意した答え：\n{context}",
        question,
        actor_id=actor_id,
        office_id=None,
    )
    if written is None:
        return None
    if len(_answers) >= ANSWER_CACHE_MAX:
        _answers.pop(next(iter(_answers)))
    _answers[key] = AUTO_ANSWER + written
    return _answers[key]
