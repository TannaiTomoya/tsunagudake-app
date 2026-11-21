import json
from datetime import UTC, date, timedelta
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import func, select
from test_identity import _agree, sign_in
from test_identity import database_app as database_app
from test_identity import identity_app as identity_app
from test_identity import mysql_identity_app as mysql_identity_app

from app.extensions import db
from app.models import LedgerAnchor, LedgerRecord, Office, OutboxMail
from app.services.anchor import anchor_ledger
from app.services.ledger import ledger_failures
from app.services.mail import mail_body
from tsunagu_rules import ledger
from tsunagu_rules.types import LedgerEntry

DAY = date(2030, 1, 7)


def _rebuild_chain(need_id: int) -> None:
    """鎖を最後まで作り直す（鎖の検証だけでは見つからない書き換え）。"""
    previous = ledger.GENESIS_HASH
    rows = db.session.scalars(
        select(LedgerRecord)
        .where(LedgerRecord.need_id == need_id)
        .order_by(LedgerRecord.seq)
    )
    for row in rows:
        data = json.loads(row.payload)
        row.created_at += timedelta(seconds=1)
        entry = LedgerEntry(
            row.seq,
            row.actor_user_id,
            row.need_id,
            row.created_at.replace(tzinfo=UTC),
            row.kind,
            data["rules"],
            data["data"],
        )
        row.payload = ledger.canonical_payload(entry)
        row.prev_hash = previous
        row.entry_hash = previous = ledger.append(previous, entry)
    db.session.commit()


def _alerts() -> list[str]:
    return [
        mail_body(mail)
        for mail in db.session.scalars(
            select(OutboxMail).where(OutboxMail.subject.contains("不一致"))
        )
    ]


def test_t55_daily_anchor_finds_a_rebuilt_chain(
    database_app: Flask, tmp_path: Path
) -> None:
    app = database_app
    _agree(app)
    with app.app_context():
        need_id = db.session.scalar(select(LedgerRecord.need_id))
        assert need_id is not None
        first = anchor_ledger(tmp_path, DAY)
        assert first.created and first.problems == [] and first.chains == 1
        stored = json.loads((tmp_path / "2030-01-07.json").read_text())
        assert stored["digest"] == first.digest and len(stored["chains"]) == 1
        # 困りごとの本文・名前・メールアドレスは、控えに入らない。
        assert set(stored) == {"date", "digest", "chains"}
        # 双方に、最後の印を知らせる（DB の外の控え）。
        notes = [
            mail_body(mail)
            for mail in db.session.scalars(
                select(OutboxMail).where(OutboxMail.digest_key.is_not(None))
            )
        ]
        assert sum(stored["chains"][0][2] in note for note in notes) == 2

        # 同じ日にもう一度動かしても、控えは1つ。上書きしない。
        again = anchor_ledger(tmp_path, DAY)
        assert not again.created and again.problems == []
        assert db.session.scalar(select(func.count()).select_from(LedgerAnchor)) == 1
        assert json.loads((tmp_path / "2030-01-07.json").read_text()) == stored

        # 次の日。何も変わっていなければ、合う。
        assert anchor_ledger(tmp_path, DAY + timedelta(days=1)).problems == []
        assert _alerts() == []

        # 鎖を最後まで作り直すと、鎖の検証は通るが、控えと合わない。
        _rebuild_chain(need_id)
        assert ledger_failures() == []
        result = anchor_ledger(tmp_path, DAY + timedelta(days=2))
        assert result.problems == [f"困りごと {need_id}：2030-01-08 の控えと合いません"]
        alerts = _alerts()
        assert len(alerts) == 1 and f"困りごと {need_id}" in alerts[0]
        row = db.session.scalar(
            select(LedgerAnchor).order_by(LedgerAnchor.anchored_on.desc())
        )
        assert row is not None and row.problems == 1
    admin = app.test_client()
    assert sign_in(admin, "admin@example.org") == 302
    page = admin.get("/admin/ledger").get_data(as_text=True)
    assert "1月9日(水)" in page and "合わない記録が1件ありました" in page


def test_t55_anchor_reports_broken_rows_and_missing_files(
    identity_app: Flask, tmp_path: Path
) -> None:
    app = identity_app
    _agree(app)
    runner = app.test_cli_runner()
    app.config["LEDGER_ANCHOR_DIR"] = str(tmp_path)
    ok = runner.invoke(args=["anchor-ledger"])
    assert ok.exit_code == 0 and "鎖1本" in ok.output, ok.output
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    with app.app_context():
        anchor = db.session.scalar(select(LedgerAnchor))
        assert anchor is not None
        # きのうの控えにして、ファイルを消す・書き換える。
        anchor.anchored_on -= timedelta(days=1)
        db.session.commit()
        yesterday = tmp_path / f"{anchor.anchored_on.isoformat()}.json"
    files[0].rename(yesterday)
    content = json.loads(yesterday.read_text())
    content["chains"][0][2] = "0" * 64
    yesterday.write_text(json.dumps(content))
    broken = runner.invoke(args=["anchor-ledger"])
    assert broken.exit_code == 1 and "を読めません" in broken.output

    yesterday.unlink()
    (tmp_path / files[0].name).unlink()
    missing = runner.invoke(args=["anchor-ledger"])
    assert missing.exit_code == 1 and "前の控えのファイルがありません" in missing.output

    # 途中の行を1つ変えても、運営に知らせが届く。
    with app.app_context():
        row = db.session.scalar(select(LedgerRecord).order_by(LedgerRecord.seq))
        assert row is not None
        row.payload = row.payload.replace("published", "edited")
        db.session.commit()
        before = len(_alerts())
    tampered = runner.invoke(args=["anchor-ledger"])
    assert tampered.exit_code == 1 and "鎖が合いません" in tampered.output
    with app.app_context():
        assert len(_alerts()) == before + 1


def test_t55_demo_chains_are_not_anchored(identity_app: Flask, tmp_path: Path) -> None:
    _agree(identity_app)
    with identity_app.app_context():
        office = db.session.get(Office, 1)
        assert office is not None
        office.is_demo = True
        db.session.commit()
        # reset-demo が作り直す台帳は、控えない（次の日に「合わない」と出さないため）。
        result = anchor_ledger(tmp_path, DAY)
        assert result.chains == 0 and result.entries == 0 and result.problems == []


def test_t55_unwritable_folder_is_reported(identity_app: Flask, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    identity_app.config["LEDGER_ANCHOR_DIR"] = str(blocker)
    result = identity_app.test_cli_runner().invoke(args=["anchor-ledger"])
    assert result.exit_code == 1 and "LEDGER_ANCHOR_DIR" in result.output
    with identity_app.app_context():
        assert db.session.scalar(select(LedgerAnchor)) is None


@pytest.mark.parametrize(
    "name", ["tsunagudake-daily.service", "tsunagudake-daily.timer"]
)
def test_t55_daily_unit_runs_before_the_backup(name: str) -> None:
    unit = (Path("deploy") / name).read_text()
    if name.endswith(".service"):
        assert "flask --app app anchor-ledger" in unit and "prune-audit" in unit
        assert "LEDGER_ANCHOR_DIR=/home/ubuntu/.local/state/" in unit
    else:
        backup = (Path("deploy") / "tsunagudake-backup.timer").read_text()
        assert "03:10:00 Asia/Tokyo" in unit and "03:30:00 Asia/Tokyo" in backup
