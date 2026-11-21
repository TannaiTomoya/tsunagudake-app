"""案内役マスへの質問と、あらかじめ書いた答え。

Flask・DB・時計を import しない。同じ質問には、いつも同じ答えを返す。
業務データ（困りごとの本文・名前・金額）は受け取らない。
"""

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Faq:
    key: str
    question: str
    answer: str
    words: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reply:
    # "faq"（用意した答え）・"boundary"（税理士の仕事）・"none"（答えが無い）・
    # "auto"（外の AI が有効のときだけ。自動で作った答え）
    kind: str
    answer: str
    key: str | None = None


QUESTION_LIMIT = 100
ANSWER_LIMIT = 80
NO_ANSWER = "その質問には、まだ答えを用意していません。下の質問から選んでください。"
BOUNDARY_ANSWER = (
    "税務の判断は、税理士の仕事です。つなぎ手も、この案内役も、お答えしません。"
)

FAQS: tuple[Faq, ...] = (
    Faq(
        "what",
        "つなぐだけは、何をするしくみですか",
        "会計とITがわかる人が毎週来て、資料回収などを仕組みにします。選ぶのも確かめるのも人です。",
        ("何をする", "どんなしくみ", "どんなサービス", "つなぐだけとは"),
    ),
    Faq(
        "zero",
        "減らなかったら、どうなりますか",
        "その月の料金は0円です。減った時間は、事務所が確かめた分だけ数えます。",
        ("減らな", "0円", "ゼロ円", "効果がな", "成果がな"),
    ),
    Faq(
        "fee",
        "料金は、どう決まりますか",
        "減った時間に時給を掛け、合意した割合（50%未満）を掛けます。1円未満は切り捨てます。",
        ("料金", "いくら", "費用", "値段", "計算"),
    ),
    Faq(
        "example",
        "試算例の32,000円は、どう計算しましたか",
        "時給4,000円 × 20時間 × 40% です。数字は架空の例です。",
        ("32,000", "32000", "試算", "例の数字"),
    ),
    Faq(
        "cap",
        "料金に上限はありますか",
        "合意のときに、月の上限を決められます。決めた場合、料金はその額までです。",
        ("上限",),
    ),
    Faq(
        "value",
        "月の価値の見込みは、約束の金額ですか",
        "約束ではありません。書いた時間から出した見込みで、料金は確かめた時間だけで決まります。",
        ("月の価値", "見込み", "約束"),
    ),
    Faq(
        "write",
        "困りごとは、どう書けばよいですか",
        "いつもの言葉で書いてください。業務の型と頼める作業に訳します。",
        ("どう書", "書き方", "何を書", "困りごとを書"),
    ),
    Faq(
        "need_example",
        "困りごとの例を教えてください",
        "たとえば「毎月、紙の領収書を集めるのに手間がかかります。写真で受け取りたいです」のように、今の作業と変えたいことを書いてください。",
        (
            "困りごとの例",
            "例が浮かば",
            "例が思いつか",
            "思い浮かば",
            "記入例",
            "具体例",
            "書く例",
        ),
    ),
    Faq(
        "need_hours",
        "かかっている時間が分からないときは？",
        "1回にかかるおよその時間に、月の回数を掛けてください。たとえば1回30分で月4回なら、月にかかる時間は2時間です。",
        (
            "時間が分から",
            "時間がわから",
            "何時間か分から",
            "何時間かわから",
            "時間の測り方",
        ),
    ),
    Faq(
        "names",
        "顧問先の名前を書いてもよいですか",
        "書かないでください。顧問先の名前と金額がなくても、頼める作業は分かります。",
        ("顧問先の名前", "名前を書", "金額を書", "個人情報"),
    ),
    Faq(
        "blocked",
        "公開できません、と出るのはなぜですか",
        "税理士の仕事にあたる言葉があるためです。どの言葉かは、その画面で確かめられます。",
        ("公開でき", "止められ", "止まる"),
    ),
    Faq(
        "publish",
        "公開すると、だれに見えますか",
        "声をかけた相手に、型・曜日・条件が見えます。本文が見えるのは、合意したあとです。",
        ("だれに見え", "誰に見え", "公開すると", "見られ"),
    ),
    Faq(
        "who",
        "候補は、だれが選んでいますか",
        "規則が理由つきで最大3人を出します。声をかける人を選ぶのは、事務所の代表です。",
        ("だれが選", "誰が選", "自動で決", "AI"),
    ),
    Faq(
        "score",
        "点数は、何で決まりますか",
        "簿記の級・会計ソフト・得意な業務・移動時間・確かめられた訪問・曜日です。理由ごとの点数は候補の表に出ます。",
        ("点数", "スコア", "順位"),
    ),
    Faq(
        "operator",
        "運営は、どこでお金を得ていますか",
        "今は取っていません。道具の月額か、自治体からの委託かを確かめています。",
        ("運営", "手数料", "取り分", "儲", "収益", "稼"),
    ),
    Faq(
        "excluded",
        "候補に出ない人がいるのはなぜですか",
        "翻訳課題が未確認の人、誓約か研修が切れた人、車で60分を超える人、曜日が合わない人は、最初から出ません。",
        ("出ない", "外れ", "条件に合わな", "候補がいな", "0人"),
    ),
    Faq(
        "connector",
        "つなぎ手は、どんな人ですか",
        "会計の言葉とITの両方がわかる人です。候補には、有効な誓約と研修、運営が確認した翻訳課題の合格が必要です。",
        ("つなぎ手", "どんな人", "協力隊"),
    ),
    Faq(
        "tax",
        "つなぎ手は、税務の相談に乗れますか",
        "乗りません。合意の台帳に「しないこと」として残ります。税務は税理士の仕事です。",
        ("税務", "申告", "節税", "税額"),
    ),
    Faq(
        "data",
        "顧問先のデータを持ち帰りますか",
        "持ち帰りません。「顧問先の生データの持ち出し」は、合意してもしないことです。",
        ("持ち帰", "持ち出", "生データ", "データを"),
    ),
    Faq(
        "ledger",
        "境界線台帳とは何ですか",
        "公開・合意・訪問・明細を、順番に残す記録です。あとから書き換えられません。",
        ("台帳", "書き換え", "改ざん", "記録"),
    ),
    Faq(
        "visit",
        "訪問は、どう数えますか",
        "つなぎ手が記録し、事務所が確かめた訪問だけを数えます。",
        ("訪問",),
    ),
    Faq(
        "reduced",
        "減った時間は、だれが決めますか",
        "事務所です。改善前と現在の時間を事務所が確かめ、その差だけを数えます。",
        ("減った時間", "だれが決", "誰が決", "確かめ"),
    ),
    Faq(
        "returned",
        "「戻った先」には、何を書きますか",
        "空いた時間を何に使えたかを、代表が選択肢から選びます。1行で書き添えることもできます。町の集計に使うのは選択肢だけです。",
        ("戻った先", "何に戻", "使い道"),
    ),
    Faq(
        "pledge",
        "誓約と研修の期限が切れると、どうなりますか",
        "候補に出なくなります。両方を有効に戻すと、また候補に出られます。",
        ("誓約", "研修", "期限"),
    ),
    Faq(
        "decline",
        "声かけは、断れますか",
        "断れます。断った困りごとの候補には、もう出ません。",
        ("断", "受けな"),
    ),
    Faq(
        "region",
        "地域の画面には、何が出ますか",
        "町ごとの合計と、直近6か月のグラフです。事務所の名前は出しません。",
        ("地域", "町の", "自治体", "ダッシュボード"),
    ),
    Faq(
        "masked",
        "数字が伏せられているのはなぜですか",
        "参加事務所が5件未満の町は、事務所が特定されないように伏せています。",
        ("伏せ", "表示しません", "少ないため", "5件"),
    ),
    Faq(
        "demo",
        "デモの数字は、本物ですか",
        "すべて架空です。名前も数字も、発表用に作ったものです。",
        ("デモ", "架空", "本物"),
    ),
    Faq(
        "masu",
        "あなたは、だれですか",
        "案内役のマスです。帳簿の升目から来ました。用意した答えの中からお答えします。",
        ("あなたは", "マスとは", "君は", "名前は"),
    ),
)

BY_KEY = {faq.key: faq for faq in FAQS}
DEFAULT_KEYS = ("what", "zero", "tax")
SCREEN_KEYS: dict[str, tuple[str, ...]] = {
    "public.index": ("what", "zero", "tax"),
    "public.how": ("fee", "operator", "score"),
    "demo.enter": ("demo", "what", "zero"),
    "auth.office_home": ("write", "who", "zero"),
    "needs.new": ("need_example", "need_hours", "names"),
    "needs.edit": ("write", "names", "blocked"),
    "needs.detail": ("value", "publish", "blocked"),
    "matching.candidates": ("who", "score", "excluded"),
    "matching.office_engagement": ("reduced", "returned", "ledger"),
    "matching.talent_engagement": ("visit", "tax", "ledger"),
    "statements.visit_form": ("visit", "reduced", "data"),
    "auth.talent_home": ("pledge", "decline", "tax"),
    "talent.profile": ("score", "pledge", "connector"),
    "talent.pledge": ("pledge", "data", "tax"),
    "auth.region_home": ("region", "masked", "connector"),
}


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    faq_keys: tuple[str, ...]


# 自分の言葉で聞くときは、先に話題を選ぶ。探す・外に渡すのは、その話題の答えだけ。
TOPICS: tuple[Topic, ...] = (
    Topic(
        "write",
        "困りごとの書き方",
        ("what", "write", "need_example", "names", "blocked", "publish"),
    ),
    Topic(
        "time_money",
        "時間とお金",
        (
            "need_hours",
            "zero",
            "fee",
            "example",
            "cap",
            "value",
            "reduced",
            "returned",
            "visit",
            "operator",
        ),
    ),
    Topic(
        "connector",
        "つなぎ手",
        ("who", "score", "excluded", "connector", "tax", "pledge", "decline"),
    ),
    Topic(
        "ledger",
        "台帳と安全",
        ("ledger", "data", "region", "masked", "demo", "masu"),
    ),
)
TOPIC_BY_KEY = {topic.key: topic for topic in TOPICS}
TOPIC_FAQS: dict[str, tuple[Faq, ...]] = {
    topic.key: tuple(BY_KEY[key] for key in topic.faq_keys) for topic in TOPICS
}
TOPIC_REQUIRED = "先に話題を選んでから、聞いてください。"
assert sorted(key for topic in TOPICS for key in topic.faq_keys) == sorted(BY_KEY)
assert len(TOPIC_REQUIRED) <= ANSWER_LIMIT


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).lower()
    return "".join(letter for letter in folded if letter.isalnum())


def _pairs(text: str) -> set[str]:
    return {text[index : index + 2] for index in range(len(text) - 1)}


def suggestions(screen: str | None, exclude: str | None = None) -> tuple[Faq, ...]:
    """その画面に合う質問を3つまで返す。知らない画面には、基本の3つ。"""
    keys = [*SCREEN_KEYS.get(screen or "", ()), *DEFAULT_KEYS, *BY_KEY]
    picked: list[Faq] = []
    for key in keys:
        if key != exclude and BY_KEY[key] not in picked:
            picked.append(BY_KEY[key])
        if len(picked) == 3:
            break
    return tuple(picked)


def answer_key(key: str) -> Reply:
    faq = BY_KEY.get(key)
    if faq is None:
        return Reply("none", NO_ANSWER)
    return Reply("faq", faq.answer, faq.key)


# 税務の話かもしれない言葉。これがある質問は、外の AI に送らない。
TAX_HINTS = ("税", "申告", "控除", "経費", "仕訳")


def topics() -> tuple[Topic, ...]:
    """自分の言葉で聞くときに選ぶ話題。"""
    return TOPICS


def faqs_in_topic(topic: str | None) -> tuple[Faq, ...] | None:
    """話題ごとの用意した答え。知らない話題は None。"""
    if not topic:
        return None
    return TOPIC_FAQS.get(topic)


def nearest(question: str, count: int = 3, topic: str | None = None) -> tuple[Faq, ...]:
    """質問に近い、用意した質問と答えを、近い順に返す。

    話題を渡したときは、その話題の答えだけを材料にする。
    """
    pool = faqs_in_topic(topic) if topic is not None else FAQS
    if not pool:
        return ()
    pairs = _pairs(_normalize(question[:QUESTION_LIMIT]))

    def score(faq: Faq) -> float:
        target = _pairs(_normalize(faq.question))
        return len(pairs & target) / (len(pairs) + len(target) or 1)

    return tuple(sorted(pool, key=score, reverse=True)[:count])


def may_send_out(question: str, taxwork: tuple[str, ...] = ()) -> bool:
    """外の AI に送ってよい質問か。税務の言葉がある質問は、送らない。"""
    text = _normalize(question[:QUESTION_LIMIT])
    words = (*TAX_HINTS, *taxwork)
    return bool(text) and not any(_normalize(word) in text for word in words)


def answer_text(
    question: str, taxwork: tuple[str, ...] = (), topic: str | None = None
) -> Reply:
    """自由に打たれた質問に、いちばん近い用意した答えを返す。

    税理士の仕事にあたる言葉（taxwork）があれば、探す前に止める。
    話題を渡したときは、その話題の答えだけを探す。話題が無い・知らないときは、探さない。
    """
    text = _normalize(question[:QUESTION_LIMIT])
    if not text:
        return Reply("none", NO_ANSWER)
    if any(_normalize(word) in text for word in taxwork):
        return Reply("boundary", BOUNDARY_ANSWER, "tax")
    pool = faqs_in_topic(topic)
    if pool is None:
        return Reply("none", TOPIC_REQUIRED)
    pairs = _pairs(text)
    best: tuple[float, Faq] | None = None
    for faq in pool:
        target = _pairs(_normalize(faq.question))
        overlap = 2 * len(pairs & target) / (len(pairs) + len(target) or 1)
        hits = sum(1 for word in faq.words if _normalize(word) in text)
        score = overlap + 0.35 * hits
        if best is None or score > best[0]:
            best = (score, faq)
    if best is None or best[0] < 0.35:
        return Reply("none", NO_ANSWER)
    return Reply("faq", best[1].answer, best[1].key)
