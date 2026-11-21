"""開発用の MCP サーバー（F90。stdio・読み取り専用・道具は3つ）。

手元の開発用 DB（`APP_ENV=development` の SQLite のファイル）だけを読む。
返す列は決まっていて、メールアドレス・名前・困りごとの本文を返さない。
本番とデモの設定（MySQL）では動かない。公開型（HTTP）にはしない。

起動：`.mcp.json` から（`uv run --frozen python tools/dev_mcp.py`）。
"""

import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402
from sqlalchemy import event, func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import ArgumentError, SQLAlchemyError  # noqa: E402

NAME = "tsunagudake-dev"
VERSION = "1"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
FAILURE_MAX = 20
ID_MAX = 2**31 - 1

TOOLS: list[dict[str, Any]] = [
    {
        "name": "open_needs",
        "description": "公開中の困りごとの件数と、型ごとの件数を返します。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_ledger",
        "description": "境界線台帳の鎖を検証し、合わない行の番号を返します。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "candidate_reasons",
        "description": "困りごとの直近の候補（順位・点数・理由）を返します。",
        "inputSchema": {
            "type": "object",
            "properties": {"need_id": {"type": "integer", "minimum": 1}},
            "required": ["need_id"],
        },
    },
]


class Refused(Exception):
    """動かしてはいけない設定。値や接続先を含めず、理由だけを伝える。"""


class ToolError(Exception):
    """道具の呼び出しを断る理由（呼んだ側に、そのまま返す）。"""


def check_environment(environ: Mapping[str, str]) -> None:
    """アプリを作る前に確かめる。開発用の SQLite のファイルでなければ断る。"""
    if environ.get("APP_ENV") != "development":
        raise Refused("APP_ENV=development のときだけ動きます。")
    _check_url(environ.get("DATABASE_URL") or "sqlite:///tsunagudake.sqlite")


def _check_url(raw: object) -> str:
    try:
        url = make_url(raw)  # type: ignore[arg-type]
    except ArgumentError, TypeError, ValueError:
        raise Refused("DATABASE_URL の形式が正しくありません。") from None
    if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
        raise Refused("手元の SQLite のファイルだけを読みます。")
    return str(url.database)


def _read_only(connection: Any, _record: object) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA query_only = ON")
    cursor.close()


def open_app() -> Flask:
    """読み取り専用でつないだアプリを返す。DB のファイルは作らない。"""
    check_environment(os.environ)
    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        # アプリが決めた接続先を、もう一度確かめる。
        path = _check_url(db.engine.url)
        if not Path(path).is_file():
            raise Refused(
                "開発用の DB がありません。先に flask db upgrade を実行してください。"
            )
        event.listen(db.engine, "connect", _read_only)
        db.engine.dispose()
    return app


def open_needs() -> dict[str, Any]:
    from app.extensions import db
    from app.models import Need, NeedStatus, NeedTranslation

    published = Need.status == NeedStatus.PUBLISHED
    total = db.session.scalar(select(func.count()).select_from(Need).where(published))
    rows = db.session.execute(
        select(
            NeedTranslation.category,
            func.count(func.distinct(NeedTranslation.need_id)),
        )
        .join(Need, Need.id == NeedTranslation.need_id)
        .where(published)
        .group_by(NeedTranslation.category)
    )
    kinds = {str(kind.value): int(count) for kind, count in rows}
    return {"published": int(total or 0), "by_kind": dict(sorted(kinds.items()))}


def verify_ledger() -> dict[str, Any]:
    from app.extensions import db
    from app.models import LedgerRecord, Need
    from app.services.ledger import ledger_failures

    failures = ledger_failures()
    return {
        "valid": not failures,
        "needs": int(db.session.scalar(select(func.count()).select_from(Need)) or 0),
        "entries": int(
            db.session.scalar(select(func.count()).select_from(LedgerRecord)) or 0
        ),
        "failure_count": len(failures),
        "failures": [
            {"need_id": need_id, "seq": seq} for need_id, seq in failures[:FAILURE_MAX]
        ],
    }


def candidate_reasons(need_id: object = None) -> dict[str, Any]:
    from app.extensions import db
    from app.models import MatchCandidate, Need
    from app.services.matching import latest_run

    if type(need_id) is not int or not 1 <= need_id <= ID_MAX:
        raise ToolError("need_id は 1 以上の整数で指定してください。")
    need = db.session.get(Need, need_id)
    if need is None:
        raise ToolError("その番号の困りごとはありません。")
    run = latest_run(need)
    candidates: list[MatchCandidate] = (
        list(
            db.session.scalars(
                select(MatchCandidate)
                .where(MatchCandidate.match_id == run.id)
                .order_by(MatchCandidate.rank)
            )
        )
        if run
        else []
    )
    return {
        "need_id": need.id,
        "need_status": str(need.status.value),
        "rules_version": run.rules_version if run else None,
        "excluded": dict(run.excluded_summary) if run else {},
        "candidates": [
            {
                "rank": row.rank,
                "talent_id": row.talent_id,
                "score": row.score,
                "status": str(row.status.value),
                "reasons": [str(reason) for reason in row.reasons],
            }
            for row in candidates
        ],
    }


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "open_needs": open_needs,
    "verify_ledger": verify_ledger,
    "candidate_reasons": candidate_reasons,
}


def call_tool(app: Flask, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """道具を1つ動かす。読むだけで、何も保存しない。"""
    from app.extensions import db

    handler = HANDLERS[name]
    allowed = TOOLS[list(HANDLERS).index(name)]["inputSchema"]["properties"]
    if set(arguments) - set(allowed):
        raise ToolError("知らない引数があります。")
    with app.app_context():
        try:
            return handler(**arguments)
        except SQLAlchemyError as error:
            # 接続先や SQL を出さない。種類だけを伝える。
            raise ToolError(
                f"DB を読めませんでした（{type(error).__name__}）。"
            ) from None
        finally:
            db.session.rollback()


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def respond(
    message: object, call: Callable[[str, Mapping[str, Any]], dict[str, Any]]
) -> dict[str, Any] | None:
    """JSON-RPC の1通に答える。通知（id が無い）には答えない。"""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "JSON-RPC 2.0 の形ではありません。")
    method = message.get("method")
    if "id" not in message:
        return None
    request_id = message["id"]
    if not isinstance(method, str):
        return _error(request_id, -32600, "method がありません。")
    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    result: dict[str, Any]
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[-1],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in HANDLERS or not isinstance(arguments, dict):
            return _error(request_id, -32602, "その道具はありません。")
        try:
            text = json.dumps(call(name, arguments), ensure_ascii=False)
            failed = False
        except ToolError as error:
            text, failed = str(error), True
        result = {"content": [{"type": "text", "text": text}], "isError": failed}
    else:
        return _error(request_id, -32601, "その操作はありません。")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    try:
        app = open_app()
    except Refused as error:
        print(f"開発用の MCP サーバーを起動しません：{error}", file=sys.stderr)
        return 2
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply: dict[str, Any] | None = _error(None, -32700, "JSON を読めません。")
        else:
            reply = respond(
                message, lambda name, arguments: call_tool(app, name, arguments)
            )
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
