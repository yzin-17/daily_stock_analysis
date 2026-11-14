#!/usr/bin/env bash
# 只在临时控制库副本中检查来源，不安装代码、不更改真实控制状态。
set -Eeuo pipefail
app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
probe_script="$app_dir/scripts/probe_backtest_daily_sources.py"
[[ -f "$probe_script" ]] || { printf '找不到受维护的来源诊断脚本。\n' >&2; exit 1; }
container_id="$(docker ps --filter label=com.docker.compose.project=thesis-ledger-dev \
  --filter label=com.docker.compose.service=dsa --filter status=running --format '{{.ID}}')"
[[ "$container_id" =~ ^[0-9a-f]{12,64}$ ]] || { printf '无法唯一确认运行中的开发DSA容器。\n' >&2; exit 1; }
# 使用 -c 让 SDK 的 spawn 子进程不尝试加载不存在的 /app/<stdin>。
docker exec -i -w /app -e PYTHONDONTWRITEBYTECODE=1 "$container_id" python -B -c \
  'import sys; exec(compile(sys.stdin.read(), "<backtest-source-probe>", "exec"))' "$@" < "$probe_script"
