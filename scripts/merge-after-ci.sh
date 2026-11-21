#!/usr/bin/env bash
# Run from the repository after pushing the feature branch.
set -euo pipefail
repo=TannaiTomoya/tsunagudake-app
stop() { echo "$*" >&2; exit 1; }

if [[ ${1:-} == --create ]]; then
    shift
    pr=$(gh pr create -R "$repo" --base main "$@")
    echo "$pr"
elif [[ $# == 1 && $1 =~ ^[0-9]+$ ]]; then
    pr=$1
else
    stop '使い方: scripts/merge-after-ci.sh PR番号 または --create --title タイトル --body-file 説明ファイル'
fi

pr_info() {
    gh pr view "$pr" -R "$repo" --json state,isDraft,baseRefName,isCrossRepository,headRefOid,headRefName,reviewDecision --jq "$1"
}
eligible='select(.state == "OPEN" and .isDraft == false and .baseRefName == "main" and .isCrossRepository == false and .reviewDecision != "CHANGES_REQUESTED" and .reviewDecision != "REVIEW_REQUIRED")'
head=$(pr_info "$eligible | .headRefOid")
[[ -n $head ]] || stop 'このPRは自動マージの対象外です。'
branch=$(pr_info '.headRefName')

# Wait for the pull_request workflow. A branch push can finish before the PR opens.
run=""
for ((attempt=0; attempt<30; attempt++)); do
    run=$(gh run list -R "$repo" -w ci.yml -e pull_request -b "$branch" -c "$head" -L 1 --json databaseId --jq '.[0].databaseId // empty')
    [[ -z $run ]] || break
    sleep 10
done
[[ -n $run ]] || stop 'PR用のCIが見つかりません。'
echo "CIを待っています https://github.com/$repo/actions/runs/$run"
gh run watch "$run" -R "$repo" --interval 12 --exit-status >/dev/null
jobs=$(gh run view "$run" -R "$repo" --json jobs --jq '[.jobs[] | select(.conclusion == "success") | .name] | contains(["sqlite","mysql 8.0","mysql 8.4"])')
[[ $jobs == true ]] || stop '必要な3件の検査がそろっていません。'
# Confirm this run belongs to this PR, then wait for any other checks on it.
links=$(gh pr view "$pr" -R "$repo" --json statusCheckRollup --jq '.statusCheckRollup[].detailsUrl')
[[ $links == *"/actions/runs/$run/job/"* ]] || stop 'このPRに紐づいた検査を確認できません。'
gh pr checks "$pr" -R "$repo" --watch --interval 12
[[ $(pr_info "$eligible | .headRefOid") == "$head" ]] || stop '待機中にPRが変更されました。'
behind=$(gh api "repos/$repo/compare/main...$head" --jq '.behind_by')
[[ $behind == 0 ]] || stop 'main が進みました。このブランチに取り込み、もう一度実行してください。'
gh pr merge "$pr" -R "$repo" --merge --match-head-commit "$head"
