"""過去比較チェック（事務所の PC の中だけで動く道具）。

使い方（Python 3.14 と、このリポジトリの `tsunagu_rules` フォルダが要ります）：
    python tools/kako_hikaku.py 試算表.csv

CSV の形（1行目は見出し。空のマスは「その期には無い」）：
    科目,今期,前期,前々期,3期前
    売上高,1200000,1000000,980000,1010000

- このプログラムは、**どこにも何も送りません**（通信の部品を読み込んでいません）。
- 科目と金額は、この画面に出るだけです。ファイルにも書きません。
- つなぐだけに入れるのは、最後の行の3つの数だけです。
- 印は「過去の幅を外れた」という事実だけです。良い・悪いを決めるのは税理士です。
"""

import argparse
import csv
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsunagu_rules.compare import (  # noqa: E402
    MARGIN_BP,
    CompareLine,
    MarkReason,
    compare,
)

PAST_PERIODS = 3


class SheetError(ValueError):
    pass


def amount(cell: str, row: int) -> int | None:
    """金額を整数にする。空は None（0 にしない）。"""
    text = unicodedata.normalize("NFKC", cell).strip().replace(",", "")
    if not text:
        return None
    negative = text.startswith(("-", "△", "▲"))
    digits = text[1:] if negative else text
    if not (digits.isascii() and digits.isdigit()):
        raise SheetError(f"{row}行目の金額を読めません。円の整数で入れてください。")
    return -int(digits) if negative else int(digits)


def read_sheet(path: Path) -> list[CompareLine]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SheetError(
            f"ファイルを読めません（UTF-8 の CSV にしてください）。{exc}"
        ) from None
    if len(rows) < 2:
        raise SheetError("見出しの行と、科目の行が要ります。")
    lines = []
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if not 2 <= len(row) <= 2 + PAST_PERIODS or not row[0].strip():
            raise SheetError(
                f"{number}行目の列を確かめてください（科目,今期,前期,前々期,3期前）。"
            )
        values = [amount(cell, number) for cell in row[1:]]
        lines.append(CompareLine(row[0].strip(), values[0], tuple(values[1:])))
    if not lines:
        raise SheetError("科目の行がありません。")
    return lines


def report(lines: list[CompareLine], margin_bp: int) -> str:
    result = compare(lines, margin_bp)
    out = ["過去比較（この画面にだけ出します。どこにも送りません）", ""]
    for mark in result.marks:
        if mark.reason == MarkReason.OUTSIDE:
            out.append(f"印  {mark.account}：過去の幅 {mark.low:,}〜{mark.high:,} の外")
        elif mark.reason == MarkReason.NO_PAST:
            out.append(f"—   {mark.account}：過去の期が無いので、比べていません")
        else:
            out.append(f"—   {mark.account}：今期が無いので、比べていません")
    if not result.marks:
        out.append("印はありません。")
    out += [
        "",
        "つなぐだけに入れる数（この3つだけ）",
        f"比べた科目 {result.compared}・印 {result.flagged}"
        f"・比べられなかった科目 {result.uncomparable}",
    ]
    return "\n".join(out)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="過去比較チェック（送信しません）")
    parser.add_argument("sheet", type=Path, help="試算表の CSV")
    parser.add_argument(
        "--margin",
        type=int,
        default=MARGIN_BP // 100,
        help="過去の平均に対する余裕（%%。既定は20）",
    )
    options = parser.parse_args(arguments)
    try:
        if not 0 <= options.margin <= 100:
            raise SheetError("余裕は 0〜100（%）で指定してください。")
        print(report(read_sheet(options.sheet), options.margin * 100))
    except SheetError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
