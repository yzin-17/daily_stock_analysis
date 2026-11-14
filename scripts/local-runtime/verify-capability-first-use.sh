#!/usr/bin/env bash
# 正式数据请求只增加验证及健康证据，不改变来源配置或路由。
set -Eeuo pipefail
app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
program="$app_dir/scripts/verify_capability_first_use.py"
container_id="$(docker ps --filter label=com.docker.compose.project=thesis-ledger-dev \
  --filter label=com.docker.compose.service=dsa --filter status=running --format '{{.ID}}')"
[[ "$container_id" =~ ^[0-9a-f]{12,64}$ ]] || { printf '无法唯一确认运行中的开发 DSA 容器。\n' >&2; exit 1; }
docker exec -i -w /app -e PYTHONDONTWRITEBYTECODE=1 "$container_id" python -B -c \
  'import sys; exec(compile(sys.stdin.read(), "<capability-first-use-verification>", "exec"))' "$@" < "$program"
