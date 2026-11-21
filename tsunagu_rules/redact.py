"""外の AI に送る前に、名前・社名・金額・連絡先の形を伏せる。純粋な関数だけ。

形で見分けるだけなので、すべては防げない。迷うときは広く伏せる。
"""

import re
import unicodedata

MASK = "〈伏せ〉"

_WORD = r"[一-龥々ぁ-んァ-ヶーA-Za-z0-9・]"
_CORPORATE = (
    r"(?:株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人"
    r"|医療法人|社会福祉法人|学校法人|宗教法人|NPO法人|\(株\)|\(有\)|\(同\))"
)
_TITLE = r"(?:様|さま|さん|氏|先生|社長|会長|専務|常務|部長|課長|店長|院長)"

_PATTERNS = (
    # メールアドレス
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    # 電話番号（区切りあり・区切りなし）
    re.compile(r"\+?\d{1,5}[-ー‐ ]\d{1,4}[-ー‐ ]\d{3,4}"),
    re.compile(r"0\d{9,10}"),
    # 金額（単位つき・記号つき・4桁以上の数字）
    re.compile(r"[¥\\]?\d[\d,]*(?:\.\d+)?\s*(?:億円|万円|千円|円|億|万)"),
    re.compile(r"[¥\\]\s*\d[\d,]*"),
    re.compile(r"\d[\d,]{3,}"),
    # 法人格の付いた社名。前に付く形も後ろに付く形もあるので、両側を伏せる
    re.compile(_WORD + r"{0,20}" + _CORPORATE + _WORD + r"{0,20}"),
    # 敬称・役職の付いた名前
    re.compile(_WORD + r"{1,10}" + _TITLE),
)


def redact(text: str) -> str:
    masked = unicodedata.normalize("NFKC", text)
    for pattern in _PATTERNS:
        masked = pattern.sub(MASK, masked)
    return re.sub(f"(?:{MASK})+", MASK, masked)
