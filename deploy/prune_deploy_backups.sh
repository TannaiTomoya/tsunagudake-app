#!/usr/bin/env bash
# 反映の前に取る DB の控えを整理する（#106）。
# 14日より古いものを消す。ただし、新しい順に3つは、古くても残す。
# 消すのは deploy.sh が作る名前の形のふつうのファイルだけ。ほかのファイルには触れない。
set -euo pipefail

dir=${1:-}
if [[ -z $dir || ! -d $dir ]]; then
    echo "使い方: bash deploy/prune_deploy_backups.sh 控えのフォルダ" >&2
    exit 2
fi

keep=3
max_age_minutes=20160 # 14日

d='[0-9]'
a='[0-9A-Za-z]'
pattern="$d$d$d$d$d$d$d${d}T$d$d$d$d$d${d}Z-$a$a$a$a$a$a$a$a.sql.gz"

shopt -s nullglob
# 名前は UTC の時刻で始まるので、並び順がそのまま古い順になる。
# shellcheck disable=SC2206  # 名前の形に合うファイルだけを広げる
files=("$dir"/$pattern)
count=${#files[@]}

for ((i = 0; i < count - keep; i++)); do
    find "${files[i]}" -maxdepth 0 -type f -mmin +"$max_age_minutes" -delete
done
