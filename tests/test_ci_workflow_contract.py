"""数据 CI 的输入过滤、分片与隔离镜像验证契约。"""

from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)


def filters():
    value = workflow()["jobs"]["changes"]
    step = next(step for step in value["steps"] if step.get("id") == "filter")
    return yaml.load(step["with"]["filters"], Loader=yaml.BaseLoader)


def test_data_ci_keeps_shards_and_failure_aggregation():
    ci = workflow()
    assert ci["on"]["pull_request"]["branches"] == ["main"]
    assert "workflow_dispatch" in ci["on"]
    jobs = ci["jobs"]
    assert jobs["changes"]["outputs"] == {
        "backend": "${{ github.event_name == 'workflow_dispatch' && 'true' || steps.filter.outputs.backend }}",
        "docker": "${{ github.event_name == 'workflow_dispatch' && 'true' || steps.filter.outputs.docker }}",
    }
    filter_step = next(step for step in jobs["changes"]["steps"] if step.get("id") == "filter")
    assert filter_step["if"] == "github.event_name == 'pull_request'"
    backend = jobs["backend-tests"]
    assert backend["runs-on"] == "ubuntu-24.04"
    assert backend["needs"] == ["changes", "ai-governance"]
    assert backend["if"] == "needs.changes.outputs.backend == 'true'"
    assert backend["strategy"]["fail-fast"] == "false"
    assert backend["strategy"]["matrix"]["shard"] == ["1", "2", "3"]
    sibling_setup = next(
        step for step in backend["steps"] if step.get("name") == "准备跨仓数据合同"
    )
    assert "if" not in sibling_setup and "../thesis-ledger" in sibling_setup["run"]
    assert "--frozen-lockfile" in sibling_setup["run"]
    assert "--recursive run build" in sibling_setup["run"]
    gate = jobs["backend-gate"]
    assert gate["needs"] == ["changes", "ai-governance", "backend-tests"]
    assert gate["if"] == "always()"
    shard = next(step for step in backend["steps"] if "PYTEST_SPLITS" in step.get("env", {}))
    assert shard["env"]["PYTEST_SPLITS"] == "3"
    assert shard["env"]["PYTEST_GROUP"] == "${{ matrix.shard }}"
    assert shard["env"]["ENV_FILE"] == "${{ runner.temp }}/dsa-data-${{ matrix.shard }}.env"
    assert ': > "$ENV_FILE"' in shard["run"]
    script = (ROOT / "scripts/ci_gate.sh").read_text()
    assert 'data_test_targets="$(python scripts/data_test_scope.py --list)"' in script
    assert "python scripts/ci_test_shard.py" in script
    assert 'check_data_service_boundary.py --syntax' in script
    assert "check_data_runtime_contract.py" in script


def test_data_assets_sdk_and_scope_changes_reach_required_gates():
    rules = filters()
    cases = {
        ".dockerignore": (True, True),
        "server.py": (True, True),
        "src/data/assets/stocks.index.json": (True, True),
        "api/data_app.py": (True, True),
        "data_provider/thesis_ledger_sources/news.py": (True, True),
        "tests/data-test-scope.json": (True, False),
        "scripts/check_data_service_boundary.py": (True, False),
        "scripts/data-runtime-contract.json": (True, True),
        "patches/longbridge-sdk/native.patch": (False, True),
        "scripts/build_thesis_ledger_longbridge_sdk.py": (True, True),
        "scripts/sdk-source.json": (True, True),
        "docs/example.md": (False, False),
    }
    for path, expected in cases.items():
        actual = tuple(any(fnmatchcase(path, pattern) for pattern in rules[gate]) for gate in ("backend", "docker"))
        assert actual == expected, (path, actual)


def test_image_smoke_uses_maintained_isolated_entrypoint():
    jobs = workflow()["jobs"]
    docker = jobs["docker-build"]
    assert docker["needs"] == ["changes", "ai-governance"]
    assert docker["if"] == "needs.changes.outputs.docker == 'true'"
    commands = "\n".join(step.get("run", "") for step in docker["steps"])
    assert "check_data_runtime_contract.py" in commands
    assert "bash scripts/verify_data_image.sh stock-analysis:test" in commands
    assert not any("docker run" in step.get("run", "") for step in docker["steps"])
