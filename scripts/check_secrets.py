"""検出した場合もファイル名だけを報告します。"""

import subprocess
import sys
from pathlib import PurePosixPath

tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
bad_names = [
    path
    for path in tracked
    if (
        PurePosixPath(path).name == ".env"
        or PurePosixPath(path).name.startswith(".env.")
    )
    and PurePosixPath(path).name != ".env.example"
]
if bad_names:
    sys.exit("Git に含まれる環境ファイル: " + ", ".join(bad_names))

pattern = "|".join(
    [
        r"-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"sk-ant-[A-Za-z0-9_-]{35,}",
    ]
)
result = subprocess.run(
    ["git", "grep", "--cached", "-IlE", "-e", pattern, "--"], check=False
)
if result.returncode != 1:
    sys.exit(1 if result.returncode == 0 else result.returncode)
