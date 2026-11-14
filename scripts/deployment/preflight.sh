#!/usr/bin/env bash
# 只读检查当前镜像是否允许载荷快更；由 infra 在持锁后调用。
set -Eeuo pipefail
[[ $# == 2 ]] || exit 2
app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
container_id="$1"
work_dir="$2"
baseline="$work_dir/dsa-runtime-inputs.json"
payload_command="$app_dir/scripts/deployment/dsa-code-payload.mjs"
node "$payload_command" contract "$app_dir"
for mapping in 'requirements.txt:dsa-requirements.txt' 'scripts/data-runtime-contract.json:dsa-runtime-contract.json'; do
  source_file="${mapping%%:*}"
  label="${mapping#*:}"
  docker cp "$container_id:/app/$source_file" "$work_dir/$label" >/dev/null
  if ! cmp -s "$app_dir/$source_file" "$work_dir/$label"; then
    printf 'DSA 兼容输入变化：%s；请使用完整 update.sh。\n' "$source_file" >&2
    exit 1
  fi
done
if ! docker cp "$container_id:/app/scripts/data-runtime-inputs.json" "$baseline" >/dev/null 2>&1; then
  printf 'DSA 镜像缺少构建兼容基线；请使用完整 update.sh。\n' >&2
  exit 1
fi
node "$payload_command" verify "$app_dir" "$baseline"
docker inspect --format '{{json .Mounts}}' "$container_id" > "$work_dir/dsa-mounts.json"
node "$payload_command" mounts "$work_dir/dsa-mounts.json"
docker inspect --format '{{json .Config.Cmd}}' "$container_id" > "$work_dir/dsa-command.json"
node --input-type=module - "$app_dir/scripts/data-runtime-contract.json" "$work_dir/dsa-command.json" <<'NODE'
import { readFileSync } from 'node:fs';
const [contract, current] = process.argv.slice(2).map(p => JSON.parse(readFileSync(p, 'utf8')));
if (JSON.stringify(contract.entrypoint) !== JSON.stringify(current)) throw new Error('DSA 启动命令不兼容快更，请使用完整 update.sh');
NODE
