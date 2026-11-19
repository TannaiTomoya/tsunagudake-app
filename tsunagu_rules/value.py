from collections.abc import Iterable

from .types import Fee, Reduction, ValueEstimate


def estimate_value(monthly_hours: int, hourly_rate_yen: int) -> ValueEstimate:
    if min(monthly_hours, hourly_rate_yen) < 0:
        raise ValueError("時間と時給は0以上にしてください。")
    low, middle, high = (monthly_hours * percent // 100 for percent in (20, 35, 50))
    return ValueEstimate(
        monthly_hours * hourly_rate_yen,
        (low, middle, high),
        (low * hourly_rate_yen, middle * hourly_rate_yen, high * hourly_rate_yen),
    )


def calc_fee(
    reductions: Iterable[Reduction],
    hourly_rate_yen: int,
    fee_share_bp: int,
    fee_cap_yen: int | None,
    *,
    fee_share_max_bp: int = 4999,
) -> Fee:
    if not 1 <= fee_share_bp <= min(fee_share_max_bp, 4999):
        raise ValueError("料金の割合が範囲外です。")
    if hourly_rate_yen < 0 or (fee_cap_yen is not None and fee_cap_yen < 0):
        raise ValueError("時給と上限は0以上にしてください。")
    reduced = 0
    for row in reductions:
        if row.confirmed:
            if min(row.before_hours, row.after_hours) < 0:
                raise ValueError("時間は0以上にしてください。")
            reduced += max(0, row.before_hours - row.after_hours)
    value = reduced * hourly_rate_yen
    fee = value * fee_share_bp // 10_000
    if fee_cap_yen is not None:
        fee = min(fee, fee_cap_yen)
    return Fee(reduced, value, fee, reduced == 0)
