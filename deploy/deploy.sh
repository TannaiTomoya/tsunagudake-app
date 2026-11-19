#!/usr/bin/env bash
set -euo pipefail
umask 077

base_url=${1:-}
checked_sha=${2:-}
if [[ ! $base_url =~ ^https://[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] ||
    (( $# < 1 || $# > 2 )) || { (( $# == 2 )) && [[ ! $checked_sha =~ ^[0-9a-f]{40}$ ]]; }; then
    echo "使い方: bash deploy/deploy.sh https://tsunagudake.example.jp [検査済みコミット]" >&2
    exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

backup_config="${BACKUP_CONFIG_DIR:-$HOME/.config/tsunagudake-backup}/my.cnf"
if [[ ! -f $backup_config || -L $backup_config || $(stat -c '%a' -- "$backup_config") != 600 ]]; then
    echo "更新前に MySQL の設定ファイルを用意してください（権限 600）。" >&2
    exit 1
fi

backup_dir="${DEPLOY_BACKUP_DIR:-$HOME/.local/state/tsunagudake/backups}"
mkdir -p -- "$backup_dir"
exec 9>"$backup_dir/.deploy.lock"
if ! flock -n 9; then
    echo "別の更新が進行中です。" >&2
    exit 1
fi

if [[ $(git symbolic-ref --quiet --short HEAD) != main ]] ||
    [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo "VPS の作業ツリーを変更のない main に戻してから反映してください。" >&2
    exit 1
fi
git fetch --no-tags origin refs/heads/main
target_sha=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
if [[ -n $checked_sha && $target_sha != "$checked_sha" ]]; then
    echo "main が検査済みのコミットから進んだため反映を見送ります。"
    exit 0
fi
if ! git merge-base --is-ancestor HEAD "$target_sha"; then
    echo "main を早送りできません。" >&2
    exit 1
fi

pending_backup=$(mktemp "$backup_dir/.pre-deploy.XXXXXXXX")
trap 'rm -f -- "$pending_backup"' EXIT
mysqldump --defaults-extra-file="$backup_config" --single-transaction \
    --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 tsunagudake | gzip > "$pending_backup"
gzip -t -- "$pending_backup"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mv -- "$pending_backup" "$backup_dir/${stamp}-${pending_backup##*.}.sql.gz"

git merge --ff-only "$target_sha"
uv sync --frozen --no-dev
uv run --frozen --no-dev flask db upgrade
sudo -n /usr/bin/systemctl restart tsunagudake.service

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
