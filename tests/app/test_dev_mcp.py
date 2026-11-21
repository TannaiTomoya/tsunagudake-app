import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from test_identity import _agree, _seed_identity, _write_need, token

from app import create_app
from app.extensions import db
from app.models import LedgerRecord, Need

TOOL = Path("tools/dev_mcp.py")
# 返してはいけないもの：メールアドレス・名前・困りごとの本文。
HIDDEN = ("@", "example.org", "架空利用者", "架空候補", "架空事務所", "機密の本文")
SECOND_BODY = "資料が集まらない。二つ目の内緒の本文です。"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("dev_mcp", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Flask, Path]:
    """手元の開発用 DB の代わりになる、架空のデータの SQLite のファイルを作る。"""
    file = tmp_path / "dev.sqlite"
    monkeypatch.setenv("APP_ENV", "test")
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{file}"})
    _seed_identity(app)
    office, _, _, _ = _agree(app)
    # 困りごと1は合意済み。困りごと2を公開中にする。
    path = _write_need(office, "/o/1/needs/new", SECOND_BODY)
    publish = office.post(path + "/publish", data={"csrf_token": token(office, path)})
    assert publish.status_code == 302
    with app.app_context():
        db.engine.dispose()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{file}")
    return app, file


def _request(method: str, request_id: int = 1, **params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"APP_ENV": "production"},
        {"APP_ENV": "demo"},
        {"APP_ENV": "test"},
        {
            "APP_ENV": "development",
            "DATABASE_URL": "mysql+pymysql://root:secret-pass@db.example.org/prod",
        },
        {"APP_ENV": "development", "DATABASE_URL": "sqlite://"},
        {"APP_ENV": "development", "DATABASE_URL": "sqlite:///:memory:"},
        {"APP_ENV": "development", "DATABASE_URL": "secret-pass://"},
    ],
)
def test_t66_refuses_everything_but_a_local_sqlite_file(
    environ: dict[str, str],
) -> None:
    mcp = _load()
    with pytest.raises(mcp.Refused) as refused:
        mcp.check_environment(environ)
    # 接続先やパスワードを、理由の文に出さない。
    assert "secret-pass" not in str(refused.value)
    assert "example.org" not in str(refused.value)


def test_t66_does_not_start_outside_development(tmp_path: Path) -> None:
    base = {
        key: value
        for key, value in os.environ.items()
        if key not in {"APP_ENV", "DATABASE_URL", "TEST_DATABASE_URL"}
    }
    missing = tmp_path / "missing.sqlite"
    cases = (
        {"APP_ENV": "production"},
        {"APP_ENV": "production", "DATABASE_URL": f"sqlite:///{missing}"},
        # 開発用でも、DB のファイルが無ければ作らない。
        {"APP_ENV": "development", "DATABASE_URL": f"sqlite:///{missing}"},
    )
    for extra in cases:
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(TOOL)],
            input=json.dumps(_request("tools/list")) + "\n",
            capture_output=True,
            text=True,
            env={**base, **extra},
            timeout=60,
        )
        assert result.returncode == 2 and result.stdout == ""
        assert "開発用の MCP サーバーを起動しません" in result.stderr
        assert str(missing) not in result.stderr and not missing.exists()


def test_t66_tools_return_fixed_columns_and_never_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_app, _ = _seeded(tmp_path, monkeypatch)
    mcp = _load()
    app = mcp.open_app()
    assert app.config["APP_ENV"] == "development"

    def ask(message: object) -> Any:
        return mcp.respond(
            message, lambda name, arguments: mcp.call_tool(app, name, arguments)
        )

    def call(name: str, **arguments: Any) -> tuple[Any, bool]:
        reply = ask(_request("tools/call", name=name, arguments=arguments))
        result = reply["result"]
        (content,) = result["content"]
        assert content["type"] == "text"
        for word in HIDDEN:
            assert word not in content["text"]
        assert "内緒" not in content["text"]
        if result["isError"]:
            return content["text"], True
        return json.loads(content["text"]), False

    # 道具は3つまで。
    started = ask(_request("initialize", protocolVersion="2025-03-26"))["result"]
    assert started["protocolVersion"] == "2025-03-26"
    assert started["capabilities"] == {"tools": {}}
    older = ask(_request("initialize", protocolVersion="1999-01-01"))["result"]
    assert older["protocolVersion"] == "2024-11-05"
    tools = ask(_request("tools/list"))["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "open_needs",
        "verify_ledger",
        "candidate_reasons",
    ]
    assert ask(_request("ping"))["result"] == {}

    assert call("open_needs") == (
        {"published": 1, "by_kind": {"collect": 1}},
        False,
    )

    ledger, failed = call("verify_ledger")
    assert not failed and set(ledger) == {
        "valid",
        "needs",
        "entries",
        "failure_count",
        "failures",
    }
    assert (ledger["valid"], ledger["needs"], ledger["failures"]) == (True, 2, [])
    assert ledger["entries"] == 4

    reasons, failed = call("candidate_reasons", need_id=1)
    assert not failed and set(reasons) == {
        "need_id",
        "need_status",
        "rules_version",
        "excluded",
        "candidates",
    }
    assert (reasons["need_id"], reasons["need_status"]) == (1, "engaged")
    assert reasons["candidates"] and len(reasons["candidates"]) <= 3
    for index, row in enumerate(reasons["candidates"], start=1):
        assert set(row) == {"rank", "talent_id", "score", "status", "reasons"}
        assert row["rank"] == index and row["reasons"]
    assert reasons["candidates"][0]["status"] == "accepted"
    # まだ候補を出していない困りごとは、空で返す。
    empty, failed = call("candidate_reasons", need_id=2)
    assert not failed and (empty["need_status"], empty["candidates"]) == (
        "published",
        [],
    )
    assert empty["rules_version"] is None and empty["excluded"] == {}

    # 引数は、決まった形だけ。
    for arguments in (
        {},
        {"need_id": 999},
        {"need_id": 0},
        {"need_id": "1"},
        {"need_id": True},
        {"need_id": 1, "columns": "email"},
    ):
        assert call("candidate_reasons", **arguments)[1] is True
    assert call("open_needs", status="draft")[1] is True
    unknown = ask(_request("tools/call", name="users", arguments={}))
    assert unknown["error"]["code"] == -32602
    assert ask(_request("resources/list"))["error"]["code"] == -32601
    assert ask({"id": 1, "method": "ping"})["error"]["code"] == -32600
    # 通知には、答えない。
    assert ask({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    # 読むだけ。書き込みは、つなぎ方の段で断られる。
    with app.app_context():
        with pytest.raises(OperationalError):
            db.session.execute(text("DELETE FROM needs"))
        db.session.rollback()
        need = db.session.get(Need, 2)
        assert need is not None
        need.monthly_hours = 1
        with pytest.raises(OperationalError):
            db.session.commit()
        db.session.rollback()
        assert db.session.scalar(select(Need.monthly_hours).where(Need.id == 2)) == 30

    # 台帳が書き換えられていたら、合わない行の番号だけを返す。
    with seed_app.app_context():
        row = db.session.scalar(
            select(LedgerRecord)
            .where(LedgerRecord.need_id == 1)
            .order_by(LedgerRecord.seq.desc())
        )
        assert row is not None
        row.payload = row.payload.replace("持ち出し", "持出し")
        db.session.commit()
    broken, failed = call("verify_ledger")
    assert not failed and broken["valid"] is False
    assert (broken["failure_count"], broken["failures"]) == (
        1,
        [{"need_id": 1, "seq": 3}],
    )


def test_t66_speaks_json_rpc_over_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seeded(tmp_path, monkeypatch)
    lines = [
        json.dumps(_request("initialize", 1, protocolVersion="2024-11-05")),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "",
        json.dumps(_request("tools/list", 2)),
        "これは JSON ではありません",
        json.dumps(_request("tools/call", 3, name="open_needs", arguments={})),
    ]
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(TOOL)],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    # 標準出力には、JSON-RPC の返事だけを書く（通知と空行には答えない）。
    assert [reply.get("id") for reply in replies] == [1, 2, None, 3]
    assert replies[0]["result"]["serverInfo"]["name"] == "tsunagudake-dev"
    assert len(replies[1]["result"]["tools"]) == 3
    assert replies[2]["error"]["code"] == -32700
    counted = json.loads(replies[3]["result"]["content"][0]["text"])
    assert counted == {"published": 1, "by_kind": {"collect": 1}}
    for word in HIDDEN:
        assert word not in result.stdout


def test_t66_settings_hold_no_keys_and_the_tool_only_reads() -> None:
    raw = Path(".mcp.json").read_text()
    settings = json.loads(raw)
    (server,) = settings["mcpServers"].values()
    assert server["command"] == "uv" and server["args"][-1] == str(TOOL)
    assert server["env"] == {"APP_ENV": "development"}
    for word in ("KEY", "SECRET", "PASSWORD", "TOKEN", "DATABASE_URL", "://"):
        assert word not in raw
    # 公開型（HTTP）にしない。.env を読まない。保存する処理を持たない。
    source = TOOL.read_text()
    for word in ("dotenv", "http", "socket", "commit(", ".add(", "delete(", "0.0.0.0"):
        assert word not in source, word
    assert "PRAGMA query_only = ON" in source
