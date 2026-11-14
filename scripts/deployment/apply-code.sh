#!/bin/sh
# 容器内事务式替换已核对的代码路径；数据、日志、报告和 OAuth 不在载荷中。
set -eu
stage="$1"
case "$stage" in /tmp/thesis-ledger-code-sync.*) ;; *) exit 2 ;; esac
backup="$stage/code-backup"
changed="$stage/changed.paths"
mkdir -p "$backup"
: > "$changed"

rollback_files() {
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    rm -rf -- "/app/$name"
    if [ -e "$backup/$name" ]; then
      mkdir -p "/app/$(dirname "$name")"
      mv "$backup/$name" "/app/$name"
    fi
  done < "$changed"
}
trap 'rollback_files' EXIT
expected='server.py
scripts/admit_nav_research.py
api
data_provider
src
scripts/data-runtime-contract.json'
test "$(cat "$stage/payload.paths")" = "$expected"
while IFS= read -r name; do
  case "$name" in
    server.py|api|data_provider|src|scripts/admit_nav_research.py|scripts/data-runtime-contract.json) ;;
    *) printf '非法 DSA 代码路径\n' >&2; exit 2 ;;
  esac
  test -e "$stage/$name"
done < "$stage/payload.paths"

while IFS= read -r name; do
  mkdir -p "$backup/$(dirname "$name")" "/app/$(dirname "$name")"
  if [ -e "/app/$name" ]; then cp -a "/app/$name" "$backup/$name"; fi
  printf '%s\n' "$name" >> "$changed"
  rm -rf -- "/app/$name"
  mv "$stage/$name" "/app/$name"
  chown -R dsa:dsa "/app/$name"
done < "$stage/payload.paths"
trap - EXIT
rm -rf -- "$stage"
