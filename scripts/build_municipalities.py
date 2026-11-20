"""総務省「都道府県コード及び市区町村コード」の Excel から、市町村の一覧を作る。

使い方：
    python scripts/build_municipalities.py 000925835.xlsx

出どころと利用の決まりは app/data/README.md に書く。標準ライブラリだけで動く。
"""

import csv
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SHEET = "xl/worksheets/sheet1.xml"  # 「現在の団体」。政令市の区は別のシートにある。
OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "municipalities.csv"
# 北方領土の6村。コードはあるが、役場が無く、住む町として選べないので入れない。
EXCLUDED = frozenset({"01695", "01696", "01697", "01698", "01699", "01700"})
EXPECTED = 1741  # 市792・町743・村183・特別区23（令和6年1月1日）
PREFECTURES = 47


def _text(node: ElementTree.Element) -> str:
    """ふりがな（rPh）を除いた文字を返す。"""
    parts = []
    for child in node:
        if child.tag == f"{MAIN}t":
            parts.append(child.text or "")
        elif child.tag == f"{MAIN}r":
            parts.extend(t.text or "" for t in child.iter(f"{MAIN}t"))
    return "".join(parts).strip()


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as book:
        shared = [
            _text(item)
            for item in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).iter(
                f"{MAIN}si"
            )
        ]
        sheet = ElementTree.fromstring(book.read(SHEET))
    for row in sheet.iter(f"{MAIN}row"):
        cells = {}
        for cell in row.iter(f"{MAIN}c"):
            column = re.match(r"[A-Z]+", cell.get("r", ""))
            value = cell.find(f"{MAIN}v")
            if column is None or value is None or value.text is None:
                continue
            cells[column.group()] = (
                shared[int(value.text)] if cell.get("t") == "s" else value.text.strip()
            )
        yield cells


def _check_digit(code: str) -> int:
    total = sum(
        int(digit) * weight for digit, weight in zip(code, (6, 5, 4, 3, 2), strict=True)
    )
    return (11 - total % 11) % 10


def build(source: Path) -> list[tuple[str, str, str]]:
    towns = []
    for cells in _rows(source):
        full, prefecture, name = (cells.get(key, "") for key in "ABC")
        if not re.fullmatch(r"[0-9]{6}", full) or not name:
            continue  # 見出しと、都道府県だけの行
        code = full[:5]
        if _check_digit(code) != int(full[5]):
            raise SystemExit(f"検査数字が合いません：{full}")
        if code in EXCLUDED:
            continue
        towns.append((code, prefecture, name))
    towns.sort()
    codes = {code for code, _, _ in towns}
    names = {(prefecture, name) for _, prefecture, name in towns}
    if (
        len(towns) != EXPECTED
        or len(codes) != EXPECTED
        or len(names) != EXPECTED
        or len({prefecture for _, prefecture, _ in towns}) != PREFECTURES
        or any(not "01" <= code[:2] <= "47" for code in codes)
        or any(len(name) > 50 or len(prefecture) > 50 for _, prefecture, name in towns)
    ):
        raise SystemExit("件数か中身が、想定と違います。Excel の版を確かめてください。")
    return towns


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    towns = build(Path(sys.argv[1]))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(("code", "prefecture", "name"))
        writer.writerows(towns)
    print(f"{len(towns)}件を {OUTPUT.name} に書きました。")


if __name__ == "__main__":
    main()
