from collections.abc import Iterable

from .types import MaskedRegionRow, RegionMetrics, RegionRow, RegionView


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
