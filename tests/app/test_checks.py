import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from test_identity import _agree, token
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import CheckSummary

TOOL = Path("tools/kako_hikaku.py")
# 架空の試算表（数字はすべて作りもの）。
SHEET = """科目,今期,前期,前々期,3期前
架空売上,1300000,1000000,980000,1010000
架空仕入,"700,000",690000,710000,
架空雑費,50000,,,
架空消耗品,,20000,21000,19000
架空支払利息,△3000,-3100,-2900,-3000
架空外注費,９００００,40000,42000,41000
"""


def _run(tmp_path: Path, text: str, *extra: str) -> subprocess.CompletedProcess[str]:
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOL), str(sheet), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_t58_tool_marks_locally_and_never_sends(tmp_path: Path) -> None:
    result = _run(tmp_path, SHEET)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # 過去の期が無い科目・今期が無い科目は、0として扱わず、比べない。
    assert "—   架空雑費：過去の期が無いので、比べていません" in out
    assert "—   架空消耗品：今期が無いので、比べていません" in out
    # 幅 = 過去の最小〜最大 ± 過去の平均の20%（切り捨て）。
    assert "印  架空売上：過去の幅 780,667〜1,209,333 の外" in out
    assert "印  架空外注費：過去の幅 31,800〜50,200 の外" in out
    assert "架空仕入" not in out and "架空支払利息" not in out
    assert out.rstrip().endswith("比べた科目 4・印 2・比べられなかった科目 2")
    # 余裕を広げると、印が減る（判断はしない。幅を外れた事実だけ）。
    wide = _run(tmp_path, SHEET, "--margin", "100")
    assert "比べた科目 4・印 1・比べられなかった科目 2" in wide.stdout

    # 通信の部品を読み込んでいない。ファイルにも書かない。
    source = TOOL.read_text(encoding="utf-8")
    for word in ("urllib", "http", "socket", "requests", "smtplib", "ftplib"):
        assert word not in source, word
    for word in (".write", "mode=", '"w"', '"a"', "shutil", "subprocess"):
        assert word not in source, word
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sheet.csv"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("科目,今期\n", "科目の行が要ります"),
        ("科目,今期,前期\n架空売上,十万,1\n", "2行目の金額を読めません"),
        ("科目,今期,前期\n架空売上,1.5,1\n", "2行目の金額を読めません"),
        ("科目,今期,前期\n,1,1\n", "2行目の列を確かめてください"),
        ("科目,今期,前期,前々期,3期前\n架空売上,1,1,1,1,1\n", "2行目の列"),
    ],
)
def test_t58_tool_rejects_unreadable_sheets(
    tmp_path: Path, text: str, message: str
) -> None:
    result = _run(tmp_path, text)
    assert result.returncode == 1 and message in result.stderr
    assert result.stdout == ""


def _record(client: FlaskClient, path: str, *counts: str, **extra: str) -> int:
    data = {
        "csrf_token": token(client, path),
        "compared": counts[0],
        "flagged": counts[1],
        "uncomparable": counts[2],
        **extra,
    }
    return client.post(path + "/checks", data=data).status_code


def test_t59_app_receives_counts_only(database_app: Flask) -> None:
    app = database_app
    office, talent, office_path, talent_path = _agree(app)
    assert "まだ記録はありません" in talent.get(talent_path).get_data(as_text=True)
    # 3つの整数だけ。印は比べた数まで。全部0は記録しない。
    for bad in (
        ("架空売上", "1", "0"),
        ("4", "5", "0"),
        ("10000", "0", "0"),
        ("-1", "0", "0"),
        ("1.5", "0", "0"),
        ("0", "0", "0"),
        ("4", "", "0"),
    ):
        assert _record(talent, talent_path, *bad) == 422, bad
    with app.app_context():
        assert db.session.scalar(select(CheckSummary)) is None
    # 科目・金額・顧問先を一緒に送っても、受け取らない（AC14）。
    assert (
        _record(
            talent,
            talent_path,
            "4",
            "2",
            "２",
            account="架空売上",
            amount="1300000",
            client_name="架空商店",
            month="2001-01",
            recorded_by="1",
            engagement_id="99",
        )
        == 302
    )
    assert _record(talent, talent_path, "6", "0", "1") == 302
    with app.app_context():
        rows = db.session.scalars(select(CheckSummary).order_by(CheckSummary.id)).all()
        assert [(r.compared, r.flagged, r.uncomparable) for r in rows] == [
            (4, 2, 2),
            (6, 0, 1),
        ]
        # 入れた人と月は、ログインと今日から決める。
        assert {r.recorded_by for r in rows} == {2}
        assert {r.month for r in rows} != {"2001-01"}
        # 文字を入れられる列は、月だけ。
        columns = {c.name: c.type.python_type for c in CheckSummary.__table__.columns}
        assert [name for name, kind in columns.items() if kind is str] == ["month"]
    for client, path in ((talent, talent_path), (office, office_path)):
        page = client.get(path).get_data(as_text=True)
        assert "<td data-numeric>2回</td><td data-numeric>10</td>" in page
        assert "架空売上" not in page and "1300000" not in page
    # 記録できるのは、担当するつなぎ手だけ。事務所の画面には入力欄が無い。
    assert 'name="compared"' not in office.get(office_path).get_data(as_text=True)
    counts = {"compared": "1", "flagged": "0", "uncomparable": "0"}
    data = {"csrf_token": token(office, office_path), **counts}
    assert office.post(talent_path + "/checks", data=data).status_code == 404
    assert office.post(office_path + "/checks", data=data).status_code == 404

    # 終わった伴走には記録できない。
    end = {"csrf_token": token(talent, talent_path), "reason": "goal_met"}
    assert talent.post(talent_path + "/end", data=end).status_code == 302
    assert _record(talent, talent_path, "1", "0", "0") == 409
    assert 'name="compared"' not in talent.get(talent_path).get_data(as_text=True)
    with app.app_context():
        assert len(db.session.scalars(select(CheckSummary)).all()) == 2
