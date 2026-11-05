"""数据镜像启动、健康、载荷和持久化约定的静态回归。"""

import json
import shutil

import pytest

from scripts.check_data_runtime_contract import ROOT, check_contract


@pytest.fixture
def fixture_root(tmp_path):
    for name in ("scripts/data-runtime-contract.json", "docker/Dockerfile", "docker/entrypoint.sh", ".dockerignore"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    contract = json.loads((tmp_path / "scripts/data-runtime-contract.json").read_text())
    for name in contract["files"] + contract["dataAssets"] + contract["nativeBuildInputs"] + contract["compatibilityInputs"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / name).is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.touch()
    for name in contract["directories"]:
        (tmp_path / name).mkdir(exist_ok=True)
    shutil.copy2(ROOT / "requirements.txt", tmp_path / "requirements.txt")
    return tmp_path


def test_repository_and_fixture_contract_are_equal(fixture_root):
    assert check_contract(ROOT) == check_contract(fixture_root)


@pytest.mark.parametrize("change", [
    "frontend", "product-copy", "startup", "health", "volume", "oauth",
    "native", "label", "missing-asset", "path-escape", "missing-baseline",
    "model-dependency", "missing-sdk", "unreferenced-product-source", "nested-bytecode", "untracked-context-rules",
])
def test_incompatible_or_unsafe_runtime_input_is_rejected(fixture_root, change):
    path = fixture_root / "docker/Dockerfile"
    text = path.read_text()
    if change == "frontend":
        text = "FROM node:20 AS web-builder\n" + text
    elif change == "product-copy":
        text += "\nCOPY bot/ ./bot/\n"
    elif change == "startup":
        text = text.replace('"server:app"', '"main:app"')
    elif change == "health":
        text = text.replace("http://localhost:8000/health", "http://localhost:8000/health || true")
    elif change == "volume":
        text = text.replace('VOLUME ["/app/data", "/app/logs", "/app/reports"]', 'VOLUME ["/app/data"]')
    elif change == "oauth":
        target = fixture_root / "docker/entrypoint.sh"
        target.write_text(target.read_text().replace(" /home/dsa/.longbridge", ""))
    elif change == "native":
        text = text.replace("OAuthBuilder.THESIS_LEDGER_STORAGE_VERSION == 1", "True")
    elif change == "label":
        text = text.replace('data-runtime="dsa-data-runtime-v1"', 'data-runtime="unknown"')
    elif change == "missing-asset":
        (fixture_root / "src/data/assets/stocks.index.json").unlink()
    elif change == "missing-baseline":
        text = text.replace("COPY --from=sdk-builder /wheels/data-runtime-inputs.json", "# removed baseline")
    elif change == "model-dependency":
        target = fixture_root / "requirements.txt"
        target.write_text(target.read_text() + "\nopenai>=1.0.0\n")
    elif change == "missing-sdk":
        target = fixture_root / "requirements.txt"
        target.write_text(target.read_text().replace("futu-api==10.8.6808", ""))
    elif change == "unreferenced-product-source":
        target = fixture_root / "src/agent/unused.py"
        target.parent.mkdir(parents=True)
        target.write_text("import openai\n")
    elif change == "nested-bytecode":
        target = fixture_root / ".dockerignore"
        target.write_text(target.read_text().replace("**/__pycache__/", "").replace("**/*.py[cod]", ""))
    elif change == "untracked-context-rules":
        target = fixture_root / "scripts/data-runtime-contract.json"
        value = json.loads(target.read_text())
        value["compatibilityInputs"].remove(".dockerignore")
        target.write_text(json.dumps(value))
    else:
        contract_path = fixture_root / "scripts/data-runtime-contract.json"
        contract = json.loads(contract_path.read_text())
        contract["files"].append("../private-input")
        contract_path.write_text(json.dumps(contract))
    path.write_text(text)
    with pytest.raises(ValueError):
        check_contract(fixture_root)
