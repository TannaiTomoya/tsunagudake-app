import re
from pathlib import Path

import pytest

from app import create_app


def test_registered_routes_match_authorization_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    document = Path("docs/authorization.md").read_text(encoding="utf-8")
    listing = document.split("### 実装済みルート一覧", 1)[1]
    entries = re.findall(r"^\| `([^`]+)` \| ([A-Z, ]+) \| §\d+ \|$", listing, re.M)
    documented = {
        (path, method) for path, methods in entries for method in methods.split(", ")
    }
    actual = {
        (rule.rule, method)
        for rule in create_app().url_map.iter_rules()
        for method in rule.methods or ()
        if method not in {"HEAD", "OPTIONS"}
    }
    assert documented == actual
