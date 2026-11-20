#!/usr/bin/env bash
set -euo pipefail
umask 077

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

config_dir="${BACKUP_CONFIG_DIR:-$HOME/.config/tsunagudake-backup}"
mysql_config="$config_dir/my.cnf"
storage_config="$config_dir/s3.env"
if [[ ! -f $storage_config || -L $storage_config || $(stat -c '%a' -- "$storage_config") != 600 ]]; then
    echo "オブジェクトストレージ用の設定ファイルを用意してください（権限 600）。" >&2
    exit 1
fi
if [[ ! -f $mysql_config || -L $mysql_config || $(stat -c '%a' -- "$mysql_config") != 600 ]]; then
    echo "バックアップ用の MySQL 設定ファイルを用意してください（権限 600）。" >&2
    exit 1
fi

# Keep scheduled backups and deployment snapshots under separate retention rules.
backup_dir="${BACKUP_STATE_DIR:-$HOME/.local/state/tsunagudake/daily-backups}"
mkdir -p -- "$backup_dir"
exec 9>"$backup_dir/.backup.lock"
if ! flock -n 9; then
    echo "バックアップがすでに実行中です。" >&2
    exit 1
fi

pending=$(mktemp "$backup_dir/.pending.XXXXXXXX")
trap 'rm -f -- "$pending"' EXIT
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$backup_dir/tsunagudake-$stamp-${pending##*.}.sql.gz"

if ! mysqldump --defaults-extra-file="$mysql_config" --single-transaction \
    --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 \
    tsunagudake | gzip > "$pending"; then
    echo "MySQL のバックアップに失敗しました。" >&2
    exit 1
fi
gzip -t -- "$pending"
mv -T --no-clobber -- "$pending" "$archive"
if [[ -e $pending ]]; then
    echo "バックアップのファイル名が重複しました。" >&2
    exit 1
fi

# Retain the complete local archive even when the remote upload fails.
find "$backup_dir" -maxdepth 1 -type f \
    -name 'tsunagudake-????????T??????Z-????????.sql.gz' -mmin +10080 -delete
uv run --locked --no-project --script upload_backup.py "$archive"
printf 'バックアップを保存しました: %s\n' "${archive##*/}"
