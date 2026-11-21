"""deploy.sh が、見た目のファイルを nginx に読める形で置くこと（runbook 注意12）。"""

from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "deploy" / "deploy.sh").read_text()


def _pos(text: str) -> int:
    index = SCRIPT.find(text)
    assert index >= 0, text
    return index


def test_deploy_keeps_umask_077_only_for_the_backup() -> None:
    assert _pos("umask 077") < _pos("mysqldump") < _pos("umask 022")


def test_deploy_restores_umask_before_fast_forward() -> None:
    assert _pos("umask 022") < _pos('git merge --ff-only "$target_sha"')


def test_deploy_repairs_static_permissions_after_fast_forward() -> None:
    repair = _pos("find app/static -type f -exec chmod 644 {} +")
    assert _pos('git merge --ff-only "$target_sha"') < repair < _pos("uv sync")
    assert ".env" not in SCRIPT[repair : _pos("uv sync")]
