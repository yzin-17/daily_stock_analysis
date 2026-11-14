#!/usr/bin/env bash
set -Eeuo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec node "$script_dir/../../../thesis-ledger-infra/scripts/infra.mjs" locked --idle -- node "$script_dir/prune-bytecode.mjs" "$@"
