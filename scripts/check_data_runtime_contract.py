"""只读核对 DSA 数据镜像、快更载荷、原生编译及持久化约定。"""

import argparse
import json
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[1]
VERSION = "dsa-data-runtime-v1"
DATA_SDKS = {
    "efinance", "akshare", "tushare", "pytdx", "baostock", "yfinance", "longbridge",
    "tickflow", "futu-api", "rqdatac", "rqdatac-fund", "cryptography",
}
PRODUCT_DEPENDENCIES = {
    "sqlalchemy", "schedule", "lark-oapi", "json-repair", "orjson", "litellm", "tiktoken",
    "openai", "anthropic", "google-genai", "google-generativeai", "pyyaml", "markdown2",
    "imgkit", "dingtalk-stream", "discord-py", "pynacl", "jinja2",
}
PRODUCT_SOURCE_ROOTS = (
    "bot", "strategies", "templates", "src/agent", "src/llm", "src/notification_sender",
    "src/services/screening", "api/v1/endpoints",
)


def check_contract(root=ROOT):
    root = Path(root)
    requirements = (root / "requirements.txt").read_text()
    names = {name.lower().replace("_", "-").replace(".", "-") for name in
             re.findall(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.-]*)", requirements)}
    if names & PRODUCT_DEPENDENCIES:
        raise ValueError("运行依赖含产品或模型包：" + ",".join(sorted(names & PRODUCT_DEPENDENCIES)))
    if not DATA_SDKS <= names:
        raise ValueError("数据 SDK 或控制加密依赖缺失：" + ",".join(sorted(DATA_SDKS - names)))
    for directory in PRODUCT_SOURCE_ROOTS:
        if any(path.suffix in {".py", ".yaml", ".yml", ".j2"} for path in (root / directory).rglob("*")):
            raise ValueError("数据打包根仍存在产品源码或模板：" + directory)
    contract = json.loads((root / "scripts/data-runtime-contract.json").read_text())
    if contract["contractVersion"] != VERSION:
        raise ValueError("数据载荷合同版本无效")
    ignores = {line.strip() for line in (root / ".dockerignore").read_text().splitlines()}
    if not {"**/__pycache__/", "**/*.py[cod]"} <= ignores:
        raise ValueError("构建上下文未递归排除宿主 Python 字节码")
    if ".dockerignore" not in contract["compatibilityInputs"]:
        raise ValueError("构建上下文规则未纳入兼容性输入")
    for key in ("files", "directories", "dataAssets", "nativeBuildInputs", "compatibilityInputs"):
        for value in contract[key]:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or not (root / path).exists():
                raise ValueError("数据载荷路径缺失或越界：" + value)
    docker = (root / "docker/Dockerfile").read_text()
    if re.search(r"(?im)^FROM\s+node|web-builder|npm\s+(?:ci|run)|COPY\s+(?:apps/|bot/|strategies/)", docker):
        raise ValueError("数据镜像仍要求前端或产品载荷")
    starts = list(re.finditer(r"(?im)^FROM\s+", docker))
    if not starts:
        raise ValueError("缺少运行镜像")
    runtime = docker[starts[-1].start():]
    copies = []
    baseline_copies = 0
    for line in runtime.splitlines():
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)
        if tokens[1].startswith("--from="):
            allowed = {"/wheels/*.whl": "/tmp/longbridge-wheels/",
                       "/wheels/data-runtime-inputs.json": contract["runtimeBaselinePath"]}
            if tokens[1] != "--from=sdk-builder" or allowed.get(tokens[2]) != tokens[-1]:
                raise ValueError("运行镜像存在未知跨阶段载荷")
            if tokens[2] == "/wheels/data-runtime-inputs.json":
                baseline_copies += 1
            continue
        copies.extend(tokens[1:-1])
    expected = set(contract["files"]) | {path + "/" for path in contract["directories"]}
    expected |= {"scripts/data-runtime-contract.json", "docker/entrypoint.sh", "requirements.txt"}
    if set(copies) != expected:
        raise ValueError("Docker 与数据载荷合同不一致")
    if baseline_copies != 1 or "data_runtime_compatibility.py --root /compat" not in docker:
        raise ValueError("镜像缺少依赖、原生 SDK 与存储结构的构建兼容基线")
    commands = re.findall(r"(?m)^CMD (\[.*\])$", runtime)
    if len(commands) != 1 or json.loads(commands[0]) != contract["entrypoint"]:
        raise ValueError("镜像默认启动与数据入口不一致")
    label = f'{contract["imageLabel"]}="{VERSION}"'
    if label not in runtime:
        raise ValueError("镜像缺少可核对的数据载荷身份")
    health = re.search(r"(?m)^HEALTHCHECK[^\n]*", runtime.replace("\\\n", " "))
    if (health is None or "||" in health.group() or "sys.exit(0)" in health.group()
            or f'curl --fail --silent --show-error http://localhost:8000{contract["healthPath"]}' not in health.group()):
        raise ValueError("镜像健康检查没有失败收敛")
    volumes = re.findall(r"(?m)^VOLUME (\[.*\])$", runtime)
    if len(volumes) != 1 or json.loads(volumes[0]) != contract["persistentDirectories"]:
        raise ValueError("镜像持久化目录发生漂移")
    entrypoint = (root / "docker/entrypoint.sh").read_text()
    match = re.search(r'^WRITABLE_DIRS="([^"]+)"$', entrypoint, re.M)
    required = set(contract["persistentDirectories"]) | {contract["oauthStorageDirectory"]}
    if match is None or set(match.group(1).split()) != required:
        raise ValueError("入口存储修复目录不符合保全约定")
    native_tokens = ("AS sdk-rust", "AS sdk-builder", "build_thesis_ledger_longbridge_sdk.py",
                     "verify_longbridge_native_storage.py", "--from=sdk-builder /wheels/*.whl",
                     "OAuthBuilder.THESIS_LEDGER_STORAGE_VERSION == 1")
    if not all(value in docker for value in native_tokens):
        raise ValueError("Longbridge 原生构建或存储接口检查缺失")
    return {"contractVersion": VERSION, "runtimeCopies": sorted(copies),
            "persistentDirectories": contract["persistentDirectories"], "healthPath": contract["healthPath"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    options = parser.parse_args()
    try:
        print(json.dumps(check_contract(options.root), ensure_ascii=False))
    except (KeyError, ValueError, OSError) as error:
        parser.exit(1, "数据载荷检查失败：" + str(error) + "\n")


if __name__ == "__main__":
    main()
