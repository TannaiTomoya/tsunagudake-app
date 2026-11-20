"""地域のグラフの座標を作る。色と文字は持たない（色は kidou.css、数字は表に書く）。"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.region import Charts

LINE_WIDTH = 600
LINE_HEIGHT = 200
_PAD_X = 30
_PAD_Y = 16
PATTERNS = ("solid", "hatch", "dots")
AFFILIATION_LABELS = ("地域おこし協力隊", "修了生", "そのほか")
UNCHOSEN_LABEL = "選んでいない"


@dataclass(frozen=True)
class Bar:
    label: str
    value: int | None
    # 一番大きい値を100とした幅。伏せたマスは0。
    percent: float


@dataclass(frozen=True)
class Segment:
    label: str
    value: int
    start: float
    width: float
    pattern: str


@dataclass(frozen=True)
class Line:
    baseline: int
    ticks: tuple[int, ...]
    # つながった月ごとの折れ線。伏せた月でとぎれる。
    paths: tuple[str, ...]
    points: tuple[tuple[int, int], ...]
    gaps: tuple[int, ...]


def _percent(value: int, whole: int) -> float:
    return round(value * 1000 / whole) / 10 if whole else 0.0


def bars(labels: Sequence[str], values: Sequence[int | None]) -> tuple[Bar, ...]:
    top = max((value for value in values if value is not None), default=0)
    return tuple(
        Bar(label, value, 0.0 if value is None else _percent(value, top))
        for label, value in zip(labels, values, strict=True)
    )


def segments(labels: Sequence[str], counts: Sequence[int]) -> tuple[Segment, ...]:
    whole = sum(counts)
    result = []
    start = 0.0
    for label, count, pattern in zip(labels, counts, PATTERNS, strict=True):
        width = _percent(count, whole)
        result.append(Segment(label, count, start, width, pattern))
        start = round((start + width) * 10) / 10
    return tuple(result)


def trend_line(values: Sequence[int | None]) -> Line:
    top = max((value for value in values if value is not None), default=0) or 1
    baseline = LINE_HEIGHT - _PAD_Y
    step = (LINE_WIDTH - 2 * _PAD_X) // max(len(values) - 1, 1)
    ticks = tuple(_PAD_X + step * index for index in range(len(values)))
    paths: list[str] = []
    run: list[str] = []
    points = []
    gaps = []
    for x, value in zip(ticks, values, strict=True):
        if value is None:
            gaps.append(x)
            if len(run) > 1:
                paths.append(" ".join(run))
            run = []
            continue
        y = baseline - (baseline - _PAD_Y) * value // top
        points.append((x, y))
        run.append(f"{x},{y}")
    if len(run) > 1:
        paths.append(" ".join(run))
    return Line(baseline, ticks, tuple(paths), tuple(points), tuple(gaps))


@dataclass(frozen=True)
class RegionFigures:
    trend: Line
    hours: tuple[Bar, ...]
    hours_total: int | None
    returned: tuple[Bar, ...]
    returned_total: int | None
    people: tuple[Segment, ...] | None
    value: tuple[Bar, ...]
    value_total: int | None


def region_figures(charts: Charts, returned_labels: Sequence[str]) -> RegionFigures:
    months = [f"{int(month[:4])}年{int(month[5:])}月" for month in charts.months]
    returned = bars([*returned_labels, UNCHOSEN_LABEL], charts.returned.values)
    return RegionFigures(
        trend_line(charts.hours.values),
        bars(months, charts.hours.values),
        charts.hours.total,
        # 選ばずに確定した明細が無ければ、その行は出さない。
        tuple(bar for bar in returned if bar.label != UNCHOSEN_LABEL or bar.value != 0),
        charts.returned.total,
        None
        if charts.affiliations is None
        else segments(AFFILIATION_LABELS, charts.affiliations),
        bars(months, charts.value_yen),
        charts.value_total,
    )
