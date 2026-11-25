"""deploy.sh が、見た目のファイルを nginx に読める形で置くこと（runbook 注意12）。"""

import os
import subprocess
import time
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


PRUNE = Path(__file__).resolve().parents[2] / "deploy" / "prune_deploy_backups.sh"
DAY = 86400


def _backup(folder: Path, number: int, days_old: float) -> Path:
    # 名前の時刻が小さいほど古い。ファイルの更新時刻も、同じ順にする。
    path = folder / f"2026010{number}T000000Z-AbCd123{number}.sql.gz"
    path.write_bytes(b"fake")
    stamp = time.time() - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


def _prune(folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(PRUNE), str(folder)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


def _names(folder: Path) -> set[str]:
    return {path.name for path in folder.iterdir()}


def test_prune_removes_old_backups_but_keeps_the_newest_three(tmp_path: Path) -> None:
    files = [_backup(tmp_path, n, d) for n, d in enumerate((40, 30, 20, 10, 1), 1)]
    assert _prune(tmp_path).returncode == 0
    # 20日前のものは、新しい順の3つに入るので残る。
    assert _names(tmp_path) == {path.name for path in files[2:]}


def test_prune_keeps_everything_within_fourteen_days(tmp_path: Path) -> None:
    files = [_backup(tmp_path, n, d) for n, d in enumerate((13, 12, 9, 5, 2, 0), 1)]
    assert _prune(tmp_path).returncode == 0
    assert _names(tmp_path) == {path.name for path in files}


def test_prune_boundary_is_fourteen_days(tmp_path: Path) -> None:
    older = _backup(tmp_path, 1, 14.1)
    younger = _backup(tmp_path, 2, 13.9)
    newest = [_backup(tmp_path, n, 1) for n in (3, 4, 5)]
    assert _prune(tmp_path).returncode == 0
    assert _names(tmp_path) == {younger.name, *(path.name for path in newest)}
    assert not older.exists()


def test_prune_keeps_three_even_if_all_are_old(tmp_path: Path) -> None:
    files = [_backup(tmp_path, n, 100 - n) for n in range(1, 5)]
    assert _prune(tmp_path).returncode == 0
    assert _names(tmp_path) == {path.name for path in files[1:]}


def test_prune_touches_only_its_own_file_names(tmp_path: Path) -> None:
    for n in range(1, 5):
        _backup(tmp_path, n, 100 - n)
    others = [
        "notes.txt",
        ".pre-deploy.AbCd1234",
        "tsunagudake-20250101T000000Z-AbCd1234.sql.gz",
        "20250101T000000Z.sql.gz",
        "abcdefghTijklmnZ-AbCd1234.sql.gz",
    ]
    for name in others:
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (time.time() - 400 * DAY,) * 2)
    outside = tmp_path.parent / "outside.txt"
    outside.write_bytes(b"keep me")
    os.utime(outside, (time.time() - 400 * DAY,) * 2)
    link = tmp_path / "20240101T000000Z-Link1234.sql.gz"
    link.symlink_to(outside)
    # 古いリンクでも、ふつうのファイルでなければ消さない。
    old = time.time() - 400 * DAY
    os.utime(link, (old, old), follow_symlinks=False)
    assert _prune(tmp_path).returncode == 0
    assert set(others) <= _names(tmp_path)
    assert link.is_symlink() and outside.read_bytes() == b"keep me"


def test_prune_handles_an_empty_or_missing_folder(tmp_path: Path) -> None:
    assert _prune(tmp_path).returncode == 0
    assert _prune(tmp_path / "missing").returncode == 2


def test_deploy_prunes_only_after_a_successful_deploy() -> None:
    call = _pos('bash deploy/prune_deploy_backups.sh "$backup_dir"')
    success = _pos("更新後の /healthz が HTTP 200 を返しました。")
    assert SCRIPT.count('prune_deploy_backups.sh "$backup_dir"') == 1
    # 控えを取る・早送り・移行のあと、/healthz が通った所でだけ消す。
    assert _pos("mysqldump") < _pos("flask db upgrade") < success < call
    assert call < _pos("更新後の /healthz が HTTP 200 を返しませんでした。")
