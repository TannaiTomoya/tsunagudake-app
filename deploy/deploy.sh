#!/usr/bin/env bash
set -euo pipefail
umask 077

base_url=${1:-}
if [[ ! $base_url =~ ^https://[a-zA-Z0-9.-]+$ ]] || (( $# != 1 )); then
    echo "使い方: bash deploy/deploy.sh https://tsunagudake.example.jp" >&2
    exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

backup_config="$HOME/.config/tsunagudake-backup/my.cnf"
if [[ ! -f $backup_config || $(stat -c '%a' -- "$backup_config") != 600 ]]; then
    echo "更新前に MySQL の設定ファイルを用意してください（権限 600）。" >&2
    exit 1
fi

backup_dir="$HOME/.local/state/tsunagudake/backups"
mkdir -p -- "$backup_dir"
exec 9>"$backup_dir/.deploy.lock"
if ! flock -n 9; then
    echo "別の更新が進行中です。" >&2
    exit 1
fi

pending_backup=$(mktemp "$backup_dir/.pre-deploy.XXXXXXXX")
trap 'rm -f -- "$pending_backup"' EXIT
mysqldump --defaults-extra-file="$backup_config" --single-transaction \
    --no-tablespaces tsunagudake | gzip > "$pending_backup"
gzip -t -- "$pending_backup"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mv -- "$pending_backup" "$backup_dir/${stamp}-${pending_backup##*.}.sql.gz"

git pull --ff-only
uv sync --frozen --no-dev
uv run --frozen --no-dev flask db upgrade
sudo systemctl restart tsunagudake

for attempt in {1..30}; do
    if status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --max-time 3 -- "$base_url/healthz") && [[ $status == 200 ]]; then
        echo "更新後の /healthz が HTTP 200 を返しました。"
        exit 0
    fi
    if (( attempt < 30 )); then
        sleep 2
    fi
done

echo "更新後の /healthz が HTTP 200 を返しませんでした。" >&2
exit 1
