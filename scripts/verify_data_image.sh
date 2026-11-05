#!/usr/bin/env bash
# 验证已存在的数据镜像；只创建自有隔离容器，不发布端口或挂载业务数据。
set -euo pipefail

build=false
if [[ "${1:-}" == "--build" ]]; then build=true; shift; fi
if [[ $# != 1 || ! "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_./:@-]*$ ]]; then
  echo "用法：bash scripts/verify_data_image.sh [--build] <本地镜像>" >&2
  exit 2
fi
image="$1"
name="dsa-data-smoke-$$-${RANDOM}"
smoke_root=""
owner_label="org.openai.thesis-ledger.test-owner"
attempts="${DSA_SMOKE_POLL_ATTEMPTS:-30}"
if [[ ! "$attempts" =~ ^[1-9][0-9]*$ ]] || (( attempts > 60 )); then
  echo "健康检查次数必须在 1～60 之间" >&2
  exit 2
fi
if [[ "$build" == true ]]; then
  repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  "${DSA_IMAGE_BUILD_PYTHON:-python}" "$repository/scripts/check_data_runtime_contract.py"
  docker build --build-arg "DSA_VERSION=${DSA_VERSION:-unconfigured}" \
    --build-arg "DSA_FORK_COMMIT=${DSA_FORK_COMMIT:-unknown}" \
    --build-arg "DSA_UPSTREAM_COMMIT=${DSA_UPSTREAM_COMMIT:-unknown}" \
    -t "$image" -f "$repository/docker/Dockerfile" "$repository"
fi
docker image inspect "$image" >/dev/null
contract="$(docker image inspect --format '{{ index .Config.Labels "org.openai.thesis-ledger.data-runtime" }}' "$image")"
if [[ "$contract" != "dsa-data-runtime-v1" ]]; then
  echo "镜像不具备已登记的数据运行合同" >&2
  exit 2
fi
if docker container inspect "$name" >/dev/null 2>&1; then
  echo "自有测试容器名称冲突，未操作现有资源" >&2
  exit 2
fi

cleanup() {
  local actual
  actual="$(docker container inspect --format "{{ index .Config.Labels \"${owner_label}\" }}" "$name" 2>/dev/null || true)"
  if [[ "$actual" == "$name" ]]; then
    docker rm -f "$name" >/dev/null
  fi
  if [[ -n "$smoke_root" ]]; then rm -rf "$smoke_root"; fi
}
trap cleanup EXIT
smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/dsa-data-smoke-env.XXXXXX")"
: > "$smoke_root/empty.env"
chmod 0444 "$smoke_root/empty.env"

docker run -d --pull=never --name "$name" --label "${owner_label}=${name}" \
  --network none --read-only \
  --tmpfs /tmp --tmpfs /app/data:rw,uid=1000,gid=1000 \
  --tmpfs /app/logs:rw,uid=1000,gid=1000 --tmpfs /app/reports:rw,uid=1000,gid=1000 \
  --tmpfs /home/dsa:rw,uid=1000,gid=1000 \
  --mount "type=bind,src=$smoke_root/empty.env,dst=/tmp/dsa-smoke.env,readonly" \
  -e PYTHONDONTWRITEBYTECODE=1 -e ENV_FILE=/tmp/dsa-smoke.env \
  -e STOCK_INDEX_REMOTE_UPDATE_ENABLED=false "$image" >/dev/null

ready=false
for (( attempt=1; attempt<=attempts; attempt++ )); do
  if docker exec -u dsa "$name" python -c \
    'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1).status == 200' \
    >/dev/null 2>&1; then
    ready=true
    break
  fi
  if (( attempt < attempts )); then sleep 1; fi
done
if [[ "$ready" != true ]]; then
  echo "数据镜像未就绪" >&2
  docker logs --tail 40 "$name" >&2
  exit 1
fi

docker exec -i -u dsa "$name" python - <<'PY'
import json
import hashlib
import marshal
from pathlib import Path
import sys
import urllib.request
from server import app
import futu
from longbridge.openapi import OAuthBuilder

assert OAuthBuilder.THESIS_LEDGER_STORAGE_VERSION == 1
baseline = json.loads(Path('/app/scripts/data-runtime-inputs.json').read_text())
assert baseline['schemaVersion'] == 'dsa-runtime-compatibility-v1'
assert baseline['contractVersion'] == 'dsa-data-runtime-v1'
assert baseline['files']
assert '.dockerignore' in baseline['files']
for name in ('requirements.txt', 'src/data/fx_rate_repository.py',
             'src/services/provider_oauth_store.py', 'src/services/thesis_ledger_control.py'):
    assert hashlib.sha256(Path('/app', name).read_bytes()).hexdigest() == baseline['files'][name]
assert not any(name in sys.modules for name in ('src.config', 'src.analyzer', 'src.notification', 'src.core.pipeline'))
for directory in ('src', 'api', 'data_provider'):
    for cached in Path('/app', directory).rglob('*.pyc'):
        original = cached.parent.parent / (cached.name.split('.')[0] + '.py')
        assert cached.parent.name == '__pycache__' and sys.implementation.cache_tag in cached.name.split('.')
        assert original.is_file() and marshal.loads(cached.read_bytes()[16:]).co_filename == str(original)
with urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=3) as response:
    paths = json.load(response)['paths']
assert '/api/v3/thesis-ledger/market/news' in paths
assert all(path.startswith('/api/v3/thesis-ledger/') or path in ('/health', '/api/health') for path in paths)
with urllib.request.urlopen('http://127.0.0.1:8000/stocks.index.json', timeout=3) as response:
    assert len(json.load(response)) > 1000
print('数据镜像入口、SDK 存储接口、路由与索引检查通过')
PY
docker stop --time 15 "$name" >/dev/null
status="$(docker container inspect --format '{{.State.ExitCode}}' "$name")"
if [[ "$status" != 0 ]]; then
  echo "数据服务未正常关闭，退出码：$status" >&2
  exit 1
fi
echo "数据镜像独立验证通过，资源将由自有标签回收"
