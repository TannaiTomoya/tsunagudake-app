from collections.abc import Iterable, Sequence
from datetime import date

from .types import (
    MaskedCells,
    MaskedRegionRow,
    RegionCell,
    RegionMetrics,
    RegionRow,
    RegionView,
)


def is_fictional(code: str) -> bool:
    """コードが 9 で始まる町だけを、架空とする（総務省のコードは 01〜47 で始まる）。"""
    return code.startswith("9")


def _values(row: RegionRow) -> tuple[int, int, int, int, int]:
    return row.offices, row.talents, row.corps_talents, row.visits, row.reduced_hours


def _mask(value: int, k: int) -> int | None:
    return None if 0 < value < k else value


def mask_region(rows: Iterable[RegionRow], k: int) -> RegionView:
    if k < 1:
        raise ValueError("匿名の基準は1以上にしてください。")
    visible = []
    totals = [0] * 5
    hidden = [0] * 5
    hidden_towns = 0
    for row in rows:
        hide_town = 0 < row.offices < k
        hidden_towns += int(hide_town)
        metrics = []
        for index, value in enumerate(_values(row)):
            if value < 0:
                raise ValueError("地域の集計値は0以上にしてください。")
            cell = None if hide_town else _mask(value, k)
            totals[index] += value
            hidden[index] += int(cell is None)
            metrics.append(cell)
        visible.append(MaskedRegionRow(row.municipality, RegionMetrics(*metrics)))
    total = None
    if totals[0] >= k and hidden_towns != 1:
        total = RegionMetrics(
            *(
                None if count == 1 else _mask(value, k)
                for value, count in zip(totals, hidden, strict=True)
            )
        )
    return RegionView(tuple(visible), total)


def recent_months(today: date, count: int) -> tuple[str, ...]:
    """今月を含む直近の月を、古い順に返す。"""
    if count < 1:
        raise ValueError("月の数は1以上にしてください。")
    index = today.year * 12 + today.month - 1
    return tuple(
        f"{step // 12:04d}-{step % 12 + 1:02d}"
        for step in range(index - count + 1, index + 1)
    )


def mask_cells(cells: Sequence[RegionCell], k: int) -> MaskedCells:
    """もとになった事務所が少ないマスを伏せる。"""
    if k < 1:
        raise ValueError("匿名の基準は1以上にしてください。")
    if any(cell.offices < 0 or cell.value < 0 for cell in cells):
        raise ValueError("地域の集計値は0以上にしてください。")
    if any(cell.value > 0 and cell.offices == 0 for cell in cells):
        raise ValueError("値のあるマスには、もとになった事務所が必要です。")
    hidden = [0 < cell.offices < k for cell in cells]
    if sum(hidden) == 1:
        # 表の合計から引くと分かってしまうので、もう1つ伏せる。
        rest = [
            index
            for index, cell in enumerate(cells)
            if not hidden[index] and cell.value > 0
        ]
        if rest:
            hidden[min(rest, key=lambda index: (cells[index].value, index))] = True
    total = None if sum(hidden) == 1 else sum(cell.value for cell in cells)
    return MaskedCells(
        tuple(
            None if hide else cell.value
            for hide, cell in zip(hidden, cells, strict=True)
        ),
        total,
    )


def mask_breakdown(counts: Sequence[int], k: int) -> tuple[int, ...] | None:
    """人数の内訳は、1つでも伏せるなら出さない。"""
    if k < 1:
        raise ValueError("匿名の基準は1以上にしてください。")
    if any(count < 0 for count in counts):
        raise ValueError("地域の集計値は0以上にしてください。")
    if any(0 < count < k for count in counts):
        return None
    return tuple(counts)
