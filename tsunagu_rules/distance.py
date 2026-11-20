"""2つの地点の距離。位置は引数でもらい、どこにも残さない。"""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000
# 「事務所の近く」とみなす半径（仮の数字）。
NEAR_RADIUS_M = 200
E6 = 1_000_000
LAT_MAX_E6 = 90 * E6
LON_MAX_E6 = 180 * E6


def _check(lat_e6: int, lon_e6: int) -> None:
    if not (
        -LAT_MAX_E6 <= lat_e6 <= LAT_MAX_E6 and -LON_MAX_E6 <= lon_e6 <= LON_MAX_E6
    ):
        raise ValueError("緯度は -90〜90、経度は -180〜180 です。")


def distance_m(lat1_e6: int, lon1_e6: int, lat2_e6: int, lon2_e6: int) -> int:
    """緯度・経度（100万分の1度の整数）から、距離（m。切り捨て）を出す。"""
    _check(lat1_e6, lon1_e6)
    _check(lat2_e6, lon2_e6)
    lat1, lon1, lat2, lon2 = (
        radians(value / E6) for value in (lat1_e6, lon1_e6, lat2_e6, lon2_e6)
    )
    half = (
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    )
    return int(2 * EARTH_RADIUS_M * asin(sqrt(min(1.0, half))))


def is_near(
    lat1_e6: int,
    lon1_e6: int,
    lat2_e6: int,
    lon2_e6: int,
    radius_m: int = NEAR_RADIUS_M,
) -> bool:
    """半径の中（境目は中）。"""
    if radius_m < 0:
        raise ValueError("半径は0以上です。")
    return distance_m(lat1_e6, lon1_e6, lat2_e6, lon2_e6) <= radius_m
