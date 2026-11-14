#!/usr/bin/env bash
# 本地部署准备接口；不创建、重启或删除容器。
set -Eeuo pipefail
app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-}"
shift || true
case "$mode" in
  sync-preflight) bash "$app_dir/scripts/deployment/preflight.sh" "$@" ;;
  sync-payload)
    [[ $# == 1 && "$1" = /* ]] || exit 2
    node "$app_dir/scripts/deployment/dsa-code-payload.mjs" build "$app_dir" "$@"
    ;;
  build-config)
    [[ $# == 1 && "$1" = /* ]] || exit 2
    node -e 'console.log(JSON.stringify({services:{dsa:{build:{additional_contexts:{longbridge_upstream:process.argv[1]+"/sdk-source"}}}}}))' "$1"
    ;;
  prepare-build)
    [[ $# == 2 && "$1" = /* && "$2" = /* ]] || exit 2
    bash "$app_dir/scripts/prepare-local-sdk-source.sh" "$1" "${LONG_BRIDGE_SOURCE_CACHE:-$2}"
    ;;
  cleanup-build)
    [[ $# == 1 && "$1" = /* ]] || exit 2
    rm -rf -- "$1/sdk-source" "$1/sdk-source-output"
    ;;
  snapshot-state|archive-state)
    [[ $# == 0 ]] || exit 2
    node "$app_dir/scripts/deployment/export-state.mjs" "$mode"
    ;;
  *) printf '用法：local-deployment.sh <sync-preflight|sync-payload|build-config|prepare-build|cleanup-build|snapshot-state|archive-state> <参数>\n' >&2; exit 2 ;;
esac
