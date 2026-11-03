#!/usr/bin/env bash
set -euo pipefail

# Temporary bridge until larksuite/cli ships drive.notice.comment_add_v1.
# Build the exact reviewed PR commit; never install it over the user's CLI.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
expected_commit="484bf1b50ad6559f234140b5fb86d9b8f276b6ae"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/futou-comment-cli.XXXXXX")"
trap 'rm -rf "$build_dir"' EXIT

git clone --quiet --filter=blob:none --no-checkout https://github.com/larksuite/cli.git "$build_dir/cli"
git -C "$build_dir/cli" fetch --quiet --depth 1 origin pull/2846/head
actual_commit="$(git -C "$build_dir/cli" rev-parse FETCH_HEAD)"
if [[ "$actual_commit" != "$expected_commit" ]]; then
  echo "文档评论 CLI PR 已更新；请先审查新提交，构建已停止。" >&2
  exit 1
fi
git -C "$build_dir/cli" checkout --quiet --detach "$expected_commit"
mkdir -p "$repo_root/.tools"
(cd "$build_dir/cli" && go build -o "$repo_root/.tools/lark-cli-comment" ./)
chmod 700 "$repo_root/.tools/lark-cli-comment"
"$repo_root/.tools/lark-cli-comment" event schema drive.notice.comment_add_v1 --json >/dev/null
echo "已构建：$repo_root/.tools/lark-cli-comment"
