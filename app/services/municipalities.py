import csv
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.extensions import db
from app.models import Municipality
from tsunagu_rules.region import is_fictional

# 総務省のコードを加工した一覧。出典は app/data/README.md。
SOURCE = Path(__file__).resolve().parent.parent / "data" / "municipalities.csv"


@dataclass(frozen=True)
class Prefecture:
    key: str  # コードの上2桁
    name: str


@dataclass(frozen=True)
class SyncResult:
    added: int
    kept: int
    different: int


def allowed(town: Municipality | None, *, demo: bool) -> bool:
    """デモは架空の町だけ、通常は実在の町だけ。"""
    return town is not None and is_fictional(town.code) == demo


def prefectures(*, demo: bool) -> list[Prefecture]:
    """選べる都道府県（デモは架空の広域）を、コードの順に返す。"""
    found: dict[str, str] = {}
    rows = db.session.execute(
        select(Municipality.code, Municipality.region_group).order_by(Municipality.code)
    )
    for code, group in rows:
        if is_fictional(code) == demo:
            found.setdefault(code[:2], group)
    return [Prefecture(key, name) for key, name in found.items()]


def towns_in(key: str, *, demo: bool) -> list[Municipality]:
    """都道府県の中の、選べる町をコードの順に返す。"""
    if not re.fullmatch(r"[0-9]{2}", key) or is_fictional(key) != demo:
        return []
    return list(
        db.session.scalars(
            select(Municipality)
            .where(Municipality.code.like(f"{key}%"))
            .order_by(Municipality.code)
        )
    )


def chosen_prefecture(
    groups: list[Prefecture], asked: str | None, municipality_id: int | None = None
) -> str:
    """画面で選んだ都道府県。無ければ今の町の都道府県、1つしか無ければそれ。"""
    keys = {group.key for group in groups}
    if asked in keys:
        return asked or ""
    town = db.session.get(Municipality, municipality_id) if municipality_id else None
    if town is not None and town.code[:2] in keys:
        return town.code[:2]
    return groups[0].key if len(groups) == 1 else ""


def load_source(path: Path = SOURCE) -> list[tuple[str, str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        rows = [
            (row["code"], row["prefecture"], row["name"])
            for row in csv.DictReader(file)
        ]
    for code, prefecture, name in rows:
        if (
            not re.fullmatch(r"[0-9]{5}", code)
            or is_fictional(code)
            or not 1 <= len(name) <= 50
            or not 1 <= len(prefecture) <= 50
        ):
            raise ValueError("市町村の一覧に、取り込めない行があります。")
    if len({code for code, _, _ in rows}) != len(rows):
        raise ValueError("市町村の一覧に、同じコードが2回あります。")
    return rows


def sync(rows: list[tuple[str, str, str]]) -> SyncResult:
    """無い行だけを1行ずつ足す。ある行は、名前が違っても直さない・消さない。"""
    existing = {
        code: (group, name)
        for code, group, name in db.session.execute(
            select(Municipality.code, Municipality.region_group, Municipality.name)
        )
    }
    added = different = 0
    for code, prefecture, name in rows:
        current = existing.get(code)
        if current is None:
            db.session.add(Municipality(code=code, name=name, region_group=prefecture))
            added += 1
        elif current != (prefecture, name):
            different += 1
    db.session.commit()
    return SyncResult(added, len(rows) - added, different)
