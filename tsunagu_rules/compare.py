"""過去比較：今期の金額が、過去の幅を外れた科目に印を付ける。

決めるのは税理士。ここは印を付けるだけで、良い・悪いを言わない。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

MARGIN_BP = 2000


class MarkReason(StrEnum):
    OUTSIDE = "outside"
    NO_PAST = "no_past"
    NO_CURRENT = "no_current"


@dataclass(frozen=True)
class CompareLine:
    account: str
    current: int | None
    past: tuple[int | None, ...]


@dataclass(frozen=True)
class CompareMark:
    account: str
    reason: MarkReason
    low: int | None = None
    high: int | None = None


@dataclass(frozen=True)
class CompareResult:
    compared: int
    flagged: int
    uncomparable: int
    marks: tuple[CompareMark, ...]


def compare(lines: Iterable[CompareLine], margin_bp: int = MARGIN_BP) -> CompareResult:
    """今期と過去（3期まで）を比べる。

    - 過去の期が1つも無い科目・今期が無い科目は、**0として扱わず**、
      「比べられない」に数える。
    - 幅は、過去の最小〜最大に、過去の平均の大きさの `margin_bp`（万分率）を足したもの。
      端数は切り捨て。
    """
    if not 0 <= margin_bp <= 10000:
        raise ValueError("余裕は 0〜10000（万分率）で指定してください。")
    compared = flagged = uncomparable = 0
    marks: list[CompareMark] = []
    for line in lines:
        past = [value for value in line.past if value is not None]
        if not past:
            uncomparable += 1
            marks.append(CompareMark(line.account, MarkReason.NO_PAST))
            continue
        if line.current is None:
            uncomparable += 1
            marks.append(CompareMark(line.account, MarkReason.NO_CURRENT))
            continue
        compared += 1
        margin = sum(abs(value) for value in past) // len(past) * margin_bp // 10000
        low, high = min(past) - margin, max(past) + margin
        if not low <= line.current <= high:
            flagged += 1
            marks.append(CompareMark(line.account, MarkReason.OUTSIDE, low, high))
    return CompareResult(compared, flagged, uncomparable, tuple(marks))
